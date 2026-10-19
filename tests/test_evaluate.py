import argparse
import csv
import json

import pytest
from fake_llm import ScriptedChatModel, call, tool_round
from langchain_core.language_models.fake_chat_models import FakeListChatModel

import evaluate
from incident_agent import data_store
from incident_agent.graph import IncidentAgent
from incident_agent.schemas import ACTIONS, CATEGORIES
from incident_agent.tools import TOOL_NAMES


def test_cases_match_the_data():
    cases = evaluate.load_cases()
    assert len(cases) == 12
    open_failures = {r["run_id"] for r in data_store.failed_runs()} | {"run-1018-transform-gold-sales"}
    for case in cases:
        assert data_store.get_run(case["run_id"])["status"] == "Failed"
        assert case["expected_category"] in CATEGORIES
        assert set(case["acceptable_actions"]) <= set(ACTIONS)
        assert set(case["expected_tools"]) <= set(TOOL_NAMES)
    assert {c["run_id"] for c in cases} == open_failures
    assert len({c["expected_category"] for c in cases}) == 12  # one case per category


def test_judge_parser():
    assert evaluate.parse_judge('thinking... {"note": 1} final {"score": 0.6, "reason": "partly"}') == {"score": 0.5, "reason": "partly"}
    with pytest.raises(ValueError):
        evaluate.parse_judge("no json")


def test_daily_limit_detection():
    assert evaluate.is_daily_limit("Error code: 429 - Rate limit reached for model x on tokens per day (TPD)")
    assert not evaluate.is_daily_limit("Error code: 429 - Rate limit reached on tokens per minute (TPM)")


def _agent_for(case_ids):
    """A scripted agent that answers each selected case correctly (one tool round + submit)."""

    cases = {c["id"]: c for c in evaluate.load_cases()}
    responses = []
    for case_id in case_ids:
        case = cases[case_id]
        responses.append(tool_round(*[call(t, **_args(t, case)) for t in ["get_pipeline_run", *case["expected_tools"]]]))
        responses.append(tool_round(call(
            "submit_diagnosis", category=case["expected_category"], root_cause=case["reference_root_cause"],
            confidence="high", evidence=["e"], fix_steps=["f"], recommended_action=case["acceptable_actions"][0],
            incident_summary="s",
        )))
    return IncidentAgent(ScriptedChatModel(responses=responses, seen=[], tool_choices=[]))


def _args(tool, case):
    run = data_store.get_run(case["run_id"])
    return {
        "get_pipeline_run": {"run_id": case["run_id"]},
        "get_recent_runs": {"pipeline_name": run["pipeline"]},
        "check_linked_service": {"name": run["linked_services"][0]},
        "compare_source_schema": {"dataset": run.get("dataset", "customers")},
        "get_job_metrics": {"job_name": run.get("databricks_job", "nb_silver_sales_join")},
        "check_landing_zone": {"path": run.get("source_file", "landing/")},
        "search_knowledge": {"query": run["error_code"]},
    }[tool]


def test_full_evaluation_run_with_scripted_agent_and_judge(tmp_path):
    ids = [c["id"] for c in evaluate.load_cases(["late-source-file", "shir-offline", "transient-network"])]
    args = argparse.Namespace(cases=ids, delay=0)
    judge = FakeListChatModel(responses=['{"score": 1, "reason": "same cause"}'] * 3)
    rows, summary, stopped = evaluate.run(args, agent=_agent_for(ids), judge=judge, log=lambda _: None)

    assert not stopped and summary["errors"] == 0
    assert summary["category_accuracy"] == summary["action_accuracy"] == summary["tool_recall"] == 1.0
    assert summary["root_cause_score"] == 1.0 and summary["completion_rate"] == 1.0

    config = {"timestamp": "t", "label": "test", "provider": "groq", "agent_model": "m", "judge_model": "j"}
    report = evaluate.write_outputs(summary, rows, config, tmp_path, update_readme=False)
    assert "Category accuracy | 100.0%" in report.read_text()
    assert json.loads((tmp_path / "latest.json").read_text())["summary"]["cases"] == 3
    assert list(csv.DictReader((tmp_path / "history.csv").open()))[0]["category_accuracy"] == "1.0"


def test_wrong_answers_are_scored_as_wrong():
    case = evaluate.load_cases(["snowflake-quota"])[0]
    outcome = {"report": {"category": "authentication", "recommended_action": "rerun", "root_cause": "x"},
               "tools_called": ["get_pipeline_run"], "seconds": 1}
    row = evaluate.score_case(case, outcome)
    assert not row["category_correct"] and not row["action_correct"] and row["tool_recall"] == 0.0


def test_repeated_errors_stop_the_run():
    class Broken:
        def diagnose(self, run_id):
            raise RuntimeError("Error code: 401 - Invalid API Key")

    args = argparse.Namespace(cases=None, delay=0)
    rows, summary, stopped = evaluate.run(args, agent=Broken(), judge=None, log=lambda _: None)
    assert stopped and len(rows) == evaluate.MAX_CONSECUTIVE_ERRORS and summary["errors"] == 3


def test_judge_failure_keeps_the_agent_result():
    class BrokenJudge:
        def invoke(self, messages):
            raise RuntimeError("Error code: 429 - Request too large")

    ids = [c["id"] for c in evaluate.load_cases(["late-source-file"])]
    rows, summary, stopped = evaluate.run(argparse.Namespace(cases=ids, delay=0), agent=_agent_for(ids), judge=BrokenJudge(), log=lambda _: None)
    assert not stopped and summary["errors"] == 0
    assert rows[0]["category_correct"] and rows[0]["root_cause_score"] is None
    assert rows[0]["judge_reason"].startswith("Judge failed")
