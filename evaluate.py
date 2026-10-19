"""Evaluate the agent on the 12 known incidents in eval/cases.json.

    python evaluate.py                      # all cases, Groq gpt-oss-120b agent, Qwen judge
    python evaluate.py --cases late-source-file shir-offline
    python evaluate.py --no-judge           # skip the LLM judge (deterministic metrics only)

Metrics
  completion rate     the agent submitted a diagnosis
  category accuracy   predicted failure category == expected
  action accuracy     recommended action is one of the acceptable actions (rerun vs fix vs escalate)
  tool recall         share of the expected evidence tools the agent actually called
  root-cause score    LLM judge (a different model family) compares the root cause with the reference: 1 / 0.5 / 0
  avg tool calls, avg seconds, forced-submit rate

Results: eval/results/latest.json, latest.md and history.csv (one row per run).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from incident_agent import create_agent
from incident_agent.config import GROQ_JUDGE_MODEL, default_agent_model, env, resolve_provider
from incident_agent.llm import make_chat_model

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "eval" / "cases.json"
RESULTS = ROOT / "eval" / "results"
README_START, README_END = "<!-- EVAL_RESULTS_START -->", "<!-- EVAL_RESULTS_END -->"
MAX_CONSECUTIVE_ERRORS = 3
HISTORY_FIELDS = [
    "timestamp", "label", "provider", "agent_model", "judge_model", "cases", "errors", "completion_rate",
    "category_accuracy", "action_accuracy", "tool_recall", "root_cause_score", "avg_tool_calls", "avg_seconds",
    "forced_submit_rate",
]

JUDGE_PROMPT = """You grade an incident-diagnosis agent. Compare the AGENT ROOT CAUSE with the REFERENCE ROOT CAUSE.
Score 1 if it identifies the same underlying cause (wording may differ), 0.5 if it is partly right or misses an
important part, 0 if it is wrong or too vague to act on. Respond with JSON only:
{"score": <0|0.5|1>, "reason": "<one short sentence>"}"""


def load_cases(selected: Sequence[str] | None = None) -> list[dict]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    if selected:
        unknown = set(selected) - {c["id"] for c in cases}
        if unknown:
            raise SystemExit(f"Unknown case ids: {', '.join(sorted(unknown))}")
        cases = [c for c in cases if c["id"] in selected]
    return cases


def parse_judge(text: str) -> dict:
    """Take the last JSON object with a score; tolerate reasoning text or code fences around it."""

    decoder, found = json.JSONDecoder(), None
    for match in re.finditer(r"\{", text or ""):
        try:
            candidate, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "score" in candidate:
            found = candidate
    if found is None:
        raise ValueError(f"Judge did not return a score: {(text or '')[:200]!r}")
    try:
        score = float(found["score"])
    except (TypeError, ValueError):
        score = 0.0
    return {"score": min((0.0, 0.5, 1.0), key=lambda allowed: abs(allowed - score)), "reason": str(found.get("reason", ""))}


def judge_root_cause(judge, case: dict, root_cause: str) -> dict:
    message = judge.invoke([
        SystemMessage(JUDGE_PROMPT),
        HumanMessage(f"REFERENCE ROOT CAUSE:\n{case['reference_root_cause']}\n\nAGENT ROOT CAUSE:\n{root_cause}"),
    ])
    content = message.content if isinstance(message.content, str) else json.dumps(message.content)
    return parse_judge(content)


def is_daily_limit(message: str) -> bool:
    lowered = message.lower().replace(" ", "")
    return ("perday" in lowered or "(tpd)" in lowered or "(rpd)" in lowered) and ("429" in lowered or "ratelimit" in lowered)


def score_case(case: dict, outcome: dict) -> dict:
    report = outcome.get("report") or {}
    called = set(outcome.get("tools_called", []))
    expected = set(case["expected_tools"])
    return {
        "id": case["id"],
        "run_id": case["run_id"],
        "completed": bool(report),
        "category": report.get("category"),
        "category_correct": report.get("category") == case["expected_category"],
        "action": report.get("recommended_action"),
        "action_correct": report.get("recommended_action") in case["acceptable_actions"],
        "tool_recall": round(len(called & expected) / len(expected), 3) if expected else 1.0,
        "tools_called": outcome.get("tools_called", []),
        "tool_calls": len(outcome.get("tools_called", [])),
        "forced_submit": bool(outcome.get("forced_submit")),
        "seconds": outcome.get("seconds"),
        "root_cause": report.get("root_cause"),
        "root_cause_score": None,
        "judge_reason": None,
        "error": None,
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def aggregate(rows: list[dict]) -> dict:
    ok = [r for r in rows if not r["error"]]
    judged = [r["root_cause_score"] for r in ok if r["root_cause_score"] is not None]
    return {
        "cases": len(rows),
        "errors": len(rows) - len(ok),
        "completion_rate": _mean([1.0 if r["completed"] else 0.0 for r in ok]),
        "category_accuracy": _mean([1.0 if r["category_correct"] else 0.0 for r in ok]),
        "action_accuracy": _mean([1.0 if r["action_correct"] else 0.0 for r in ok]),
        "tool_recall": _mean([r["tool_recall"] for r in ok]),
        "root_cause_score": _mean(judged),
        "avg_tool_calls": _mean([float(r["tool_calls"]) for r in ok]),
        "avg_seconds": _mean([float(r["seconds"] or 0) for r in ok]),
        "forced_submit_rate": _mean([1.0 if r["forced_submit"] else 0.0 for r in ok]),
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def render_markdown(summary: dict, rows: list[dict], config: dict) -> str:
    lines = [
        "# Agent evaluation results",
        "",
        f"- Run: {config['timestamp']} · {summary['cases']} incidents · errors: {summary['errors']}  ",
        f"- Agent: `{config['agent_model']}` via {config['provider']} · judge: `{config['judge_model'] or 'none'}`",
        "",
        "| Metric | Score |",
        "|---|---|",
        f"| Diagnosis completed | {_pct(summary['completion_rate'])} |",
        f"| Category accuracy | {_pct(summary['category_accuracy'])} |",
        f"| Recommended action correct | {_pct(summary['action_accuracy'])} |",
        f"| Evidence tool recall | {_pct(summary['tool_recall'])} |",
        f"| Root-cause score (LLM judge) | {_pct(summary['root_cause_score'])} |",
        f"| Avg tool calls per incident | {summary['avg_tool_calls'] if summary['avg_tool_calls'] is not None else 'n/a'} |",
        f"| Avg seconds per incident | {summary['avg_seconds'] if summary['avg_seconds'] is not None else 'n/a'} |",
        f"| Forced to submit (budget or no tool call) | {_pct(summary['forced_submit_rate'])} |",
        "",
        "## Per-incident results",
        "",
        "| Case | Category | Action | Tool recall | Root cause | Tool calls | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        if r["error"]:
            lines.append(f"| {r['id']} | error | | | | | {r['error'][:100].replace('|', '/')} |")
            continue
        category = f"{'✅' if r['category_correct'] else '❌'} {r['category']}"
        action = f"{'✅' if r['action_correct'] else '❌'} {r['action']}"
        score = "" if r["root_cause_score"] is None else f"{r['root_cause_score']:g}"
        note = (r["judge_reason"] or "").replace("|", "/")[:110]
        lines.append(f"| {r['id']} | {category} | {action} | {r['tool_recall']:.0%} | {score} | {r['tool_calls']} | {note} |")
    lines.append("")
    return "\n".join(lines)


def write_outputs(summary: dict, rows: list[dict], config: dict, output_dir: Path, update_readme: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "latest.json").write_text(json.dumps({"config": config, "summary": summary, "results": rows}, indent=2), encoding="utf-8")
    markdown = render_markdown(summary, rows, config)
    (output_dir / "latest.md").write_text(markdown, encoding="utf-8")
    history = output_dir / "history.csv"
    new = not history.exists()
    with history.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=HISTORY_FIELDS, extrasaction="ignore")
        if new:
            writer.writeheader()
        writer.writerow({**config, **summary})
    if update_readme:
        readme = ROOT / "README.md"
        text = readme.read_text(encoding="utf-8")
        if README_START in text and README_END in text:
            table = markdown.split("## Per-incident results")[0].replace("# Agent evaluation results", "**Latest results**").strip()
            block = f"{README_START}\n{table}\n\nPer-incident results: `eval/results/latest.md`.\n{README_END}"
            start, end = text.index(README_START), text.index(README_END) + len(README_END)
            readme.write_text(text[:start] + block + text[end:], encoding="utf-8")
    return output_dir / "latest.md"


def run(args: argparse.Namespace, agent=None, judge=None, sleep=time.sleep, log=print) -> tuple[list[dict], dict, bool]:
    cases = load_cases(args.cases)
    rows: list[dict] = []
    consecutive_errors, stopped = 0, False
    for number, case in enumerate(cases, start=1):
        if number > 1 and args.delay:
            sleep(args.delay)
        try:
            outcome = agent.diagnose(run_id=case["run_id"])
            row = score_case(case, outcome)
            if judge is not None and row["root_cause"]:
                try:
                    verdict = judge_root_cause(judge, case, row["root_cause"])
                    row["root_cause_score"], row["judge_reason"] = verdict["score"], verdict["reason"]
                except Exception as exc:  # noqa: BLE001 - a judge failure must not discard the agent's result
                    row["judge_reason"] = f"Judge failed: {type(exc).__name__}: {str(exc)[:150]}"
            consecutive_errors = 0
        except Exception as exc:  # noqa: BLE001 - provider errors
            row = {**score_case(case, {}), "error": f"{type(exc).__name__}: {exc}"[:400]}
            consecutive_errors += 1
        rows.append(row)
        if row["error"]:
            log(f"[{number:02d}/{len(cases)}] {case['id']}: ERROR {row['error']}")
            if is_daily_limit(row["error"]) or consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                log("Stopping: repeated errors or a daily rate limit. Try again later.")
                stopped = True
                break
        else:
            log(
                f"[{number:02d}/{len(cases)}] {case['id']}: category={row['category']} ({'ok' if row['category_correct'] else 'wrong'}), "
                f"action={row['action']} ({'ok' if row['action_correct'] else 'wrong'}), tools={row['tool_calls']}, "
                f"root_cause={row['root_cause_score']}, {row['seconds']}s"
            )
    return rows, aggregate(rows), stopped


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", nargs="*", help="Case ids to run (default: all)")
    parser.add_argument("--provider", choices=["groq", "gemini"])
    parser.add_argument("--model", help="Agent model (default: openai/gpt-oss-120b on Groq)")
    parser.add_argument("--judge-model", default=None, help=f"Judge model (default: {GROQ_JUDGE_MODEL} on Groq)")
    parser.add_argument("--no-judge", action="store_true")
    parser.add_argument("--delay", type=float, default=20.0, help="Seconds between incidents, to stay inside free-tier per-minute limits")
    parser.add_argument("--label", default="")
    parser.add_argument("--update-readme", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = parse_args(argv)
    provider = resolve_provider(args.provider)
    if not env("GROQ_API_KEY" if provider == "groq" else "GEMINI_API_KEY"):
        print(f"Set {'GROQ_API_KEY' if provider == 'groq' else 'GEMINI_API_KEY'} first.", file=sys.stderr)
        return 2
    agent_model = args.model or default_agent_model(provider)
    judge_model = "" if args.no_judge else (args.judge_model or (GROQ_JUDGE_MODEL if provider == "groq" else agent_model))
    agent = create_agent(provider=provider, model=agent_model)
    judge = None if args.no_judge else make_chat_model(provider, judge_model, max_tokens=300)

    print(f"Evaluating {agent_model} ({provider}) on {len(load_cases(args.cases))} incidents; judge: {judge_model or 'none'}")
    rows, summary, stopped = run(args, agent=agent, judge=judge)
    config = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "label": args.label,
        "provider": provider,
        "agent_model": agent_model,
        "judge_model": judge_model,
    }
    output_dir = RESULTS / "failed-run" if stopped else RESULTS
    report = write_outputs(summary, rows, config, output_dir, update_readme=args.update_readme and not stopped)
    print()
    print(report.read_text(encoding="utf-8"))
    return 1 if stopped or summary["errors"] == summary["cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
