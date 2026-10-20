"""Command line: list open failures or diagnose one.

    python -m incident_agent.cli list
    python -m incident_agent.cli diagnose run-1019-ingest-inventory
    python -m incident_agent.cli diagnose --error "Java heap space in nb_silver_sales_join"
    python -m incident_agent.cli diagnose run-1019-ingest-inventory --approve
    python -m incident_agent.cli --env lakehouse list
    python -m incident_agent.cli --env lakehouse diagnose run-2026-07-22-153512
"""

from __future__ import annotations

import argparse
import json
import sys

from . import create_agent, data_store, lakehouse_store
from .environments import ENVIRONMENTS, run_label


def _print_report(report: dict) -> None:
    print(f"\nCategory:    {report['category']}  (confidence: {report['confidence']})")
    print(f"Root cause:  {report['root_cause']}")
    print(f"Action:      {report['recommended_action']}")
    for title, key in (("Evidence", "evidence"), ("Fix steps", "fix_steps"), ("Prevention", "prevention"), ("Sources", "sources")):
        if report.get(key):
            print(f"\n{title}:")
            for number, item in enumerate(report[key], start=1):
                print(f"  {number}. {item}")
    print(f"\nTicket summary:\n  {report['incident_summary']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env", choices=list(ENVIRONMENTS), default="azure", help="Platform to investigate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List open pipeline failures")
    diagnose = sub.add_parser("diagnose", help="Diagnose a failed run")
    diagnose.add_argument("run_id", nargs="?")
    diagnose.add_argument("--error", help="Free-text error message instead of (or in addition to) a run id")
    diagnose.add_argument("--provider", choices=["groq", "gemini"])
    diagnose.add_argument("--model")
    diagnose.add_argument("--approve", action="store_true", help="Approve a recommended rerun without asking")
    diagnose.add_argument("--json", action="store_true", help="Print the report as JSON")
    args = parser.parse_args(argv)

    if args.command == "list":
        failures = lakehouse_store.failed_runs() if args.env == "lakehouse" else data_store.failed_runs()
        for run in failures:
            print(f"{run['run_id']:38} {run_label(args.env, run)}")
        return 0

    if not args.run_id and not args.error:
        parser.error("give a run id or --error")

    agent = create_agent(provider=args.provider, model=args.model, environment=args.env)
    thread_id, report, pending = None, None, None
    for event in agent.stream(run_id=args.run_id, error_text=args.error):
        if event["type"] == "start":
            thread_id = event["thread_id"]
        elif event["type"] == "tool_call":
            print(f"-> {event['tool']}({json.dumps(event['args'])})")
        elif event["type"] == "report":
            report = event["report"]
        elif event["type"] == "approval_required":
            pending = event

    if report is None:
        print("The agent did not produce a diagnosis.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_report(report)

    if pending:
        approved = args.approve
        if not approved and sys.stdin.isatty():
            approved = input(f"\n{pending['question']} [y/N] ").strip().lower() == "y"
        for event in agent.resume(thread_id, approved=approved, approver="cli user"):
            if event["type"] == "action_result":
                print(f"\nRerun: {json.dumps(event['result'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
