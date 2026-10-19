"""Tests for the lakehouse environment, using the real run logs in lakehouse/monitoring/."""

import json

import pytest
from fake_llm import ScriptedChatModel, call, final_text, report_json, tool_round
from langchain_core.messages import SystemMessage

import evaluate
from incident_agent import lakehouse_store, lakehouse_tools
from incident_agent.environments import ENVIRONMENTS, get_environment, run_label, tool_names
from incident_agent.graph import IncidentAgent
from incident_agent.schemas import ACTIONS, CATEGORIES


pytestmark = pytest.mark.skipif(
    not (lakehouse_store.monitoring_dir() / "index.json").exists()
    or not any(c.get("environment") == "lakehouse" for c in evaluate.load_cases()),
    reason="lakehouse run logs not synced yet",
)


def lakehouse_cases():
    return evaluate.load_cases(environment="lakehouse")


def case(case_id):
    return next(c for c in lakehouse_cases() if c["id"] == case_id)


def scripted(*responses):
    return ScriptedChatModel(responses=list(responses), seen=[], tool_choices=[])


def test_run_logs_are_vendored_and_cases_point_at_failed_runs():
    runs = lakehouse_store.all_runs()
    assert sum(r["status"] == "SUCCEEDED" for r in runs) >= 2
    failed = {r["run_id"] for r in lakehouse_store.failed_runs()}
    cases = lakehouse_cases()
    assert len(cases) == 5
    for item in cases:
        assert item["run_id"] in failed
        assert item["expected_category"] in CATEGORIES
        assert set(item["acceptable_actions"]) <= set(ACTIONS)
        assert set(item["expected_tools"]) <= set(tool_names("lakehouse"))


def test_run_logs_do_not_reveal_the_injected_scenario():
    text = " ".join(
        path.read_text(encoding="utf-8") for path in lakehouse_store.monitoring_dir().rglob("*.json")
    ).lower()
    for marker in ("scenario", "inject", "new_column_in_products", "truncated_payments", "duplicate_orders"):
        assert marker not in text


def test_failed_run_has_stage_error_and_skipped_stages():
    run = lakehouse_store.get_run(case("lakehouse-text-in-price")["run_id"])
    assert run["status"] == "FAILED"
    failed = [s for s in run["stages"] if s["status"] == "FAILED"]
    assert len(failed) == 1 and failed[0]["module"] == "src.incremental"
    assert any("N/A" in line for line in failed[0]["key_error_lines"])
    assert all(s["status"] == "SKIPPED" for s in run["stages"][1:])


def test_source_file_checks_find_each_problem():
    files = lambda case_id: {  # noqa: E731
        f["dataset"]: f for f in lakehouse_store.source_files(case(case_id)["run_id"])["files"]
    }
    assert files("lakehouse-new-column")["products"]["added_columns"] == ["currency"]
    price = files("lakehouse-text-in-price")["products"]["values_not_matching_schema_type"]
    assert price[0]["column"] == "price" and "N/A" in price[0]["examples"]
    assert files("lakehouse-duplicate-orders")["orders"]["duplicate_primary_keys"] == 800
    payments = files("lakehouse-truncated-payments")["payments"]
    assert payments["rows_with_wrong_column_count"] >= 1 and payments["ends_with_newline"] is False


def test_restart_stage_for_a_run_started_too_late():
    run_id = case("lakehouse-wrong-start-stage")["run_id"]
    assert lakehouse_store.get_run(run_id)["start_from"] == "src.gold"
    assert lakehouse_store.restart_stage(run_id)["module"] == "src.incremental"
    assert lakehouse_store.rerun_command(run_id) == "python run_pipeline.py --batch-date 2026-07-22"


def test_tool_outputs_are_compact_json():
    run_id = case("lakehouse-truncated-payments")["run_id"]
    outputs = [
        lakehouse_tools.get_lakehouse_run.invoke({"run_id": run_id}),
        lakehouse_tools.get_stage_log.invoke({"run_id": run_id, "stage": "src.incremental"}),
        lakehouse_tools.get_recent_lakehouse_runs.invoke({}),
        lakehouse_tools.check_source_files.invoke({"run_id": run_id}),
        lakehouse_tools.check_source_files.invoke({"run_id": run_id, "dataset": "payments"}),
        lakehouse_tools.get_validation_report.invoke({"run_id": run_id}),
        lakehouse_tools.get_gold_quality.invoke({"run_id": run_id}),
        lakehouse_tools.get_layer_outputs.invoke({"run_id": run_id}),
    ]
    for output in outputs:
        assert len(output) <= 1800
        if not output.endswith("(truncated)"):
            json.loads(output)


def test_unknown_run_and_stage_give_helpful_messages():
    assert "No lakehouse run" in lakehouse_tools.get_lakehouse_run.invoke({"run_id": "run-nope"})
    run_id = case("lakehouse-new-column")["run_id"]
    assert "Stages:" in lakehouse_tools.get_stage_log.invoke({"run_id": run_id, "stage": "bogus"})


def test_environments_have_their_own_tools_and_labels():
    assert set(ENVIRONMENTS) == {"azure", "lakehouse"}
    assert "get_lakehouse_run" in tool_names("lakehouse") and "get_lakehouse_run" not in tool_names("azure")
    assert "search_knowledge" in tool_names("lakehouse")
    run = lakehouse_store.failed_runs()[0]
    assert run["batch_date"] in run_label("lakehouse", run)
    assert get_environment(None).key == "azure"


def test_lakehouse_investigation_uses_lakehouse_prompt_and_approval_command():
    run_id = case("lakehouse-wrong-start-stage")["run_id"]
    llm = scripted(
        tool_round(call("get_lakehouse_run", run_id=run_id)),
        tool_round(call("get_layer_outputs", run_id=run_id),
                   call("search_knowledge", query="FileNotFoundError SCD2 dimension not found start-from")),
        final_text("Started from src.gold; earlier layers missing. Rerun the full batch."),
        report_json(category="upstream_dependency", root_cause="Run started from src.gold; SCD2 output missing.",
                    confidence="high", evidence=["start_from src.gold"], fix_steps=["Rerun the full batch"],
                    recommended_action="rerun", incident_summary="Wrong start stage."),
    )
    agent = IncidentAgent(llm)
    result = agent.diagnose(run_id=run_id, environment="lakehouse")
    assert result["tools_called"] == ["get_lakehouse_run", "get_layer_outputs", "search_knowledge"]
    assert isinstance(llm.seen[0][0], SystemMessage) and "Retail Analytics Lakehouse" in llm.seen[0][0].content
    pending = result["pending_approval"]
    assert pending["environment"] == "lakehouse"
    assert "python run_pipeline.py --batch-date 2026-07-22" in pending["question"]

    events = list(agent.resume(result["thread_id"], approved=True, approver="Piyush"))
    action = next(e for e in events if e["type"] == "action_result")["result"]
    assert action["status"] == "Queued" and action["command"] == "python run_pipeline.py --batch-date 2026-07-22"
    assert any(r["rerun_id"] == action["rerun_id"] for r in lakehouse_store.rerun_log())


def test_knowledge_base_has_the_lakehouse_runbook():
    from incident_agent.knowledge import default_knowledge_base

    results = default_knowledge_base().search("MALFORMED_CSV_RECORD truncated file last row", source="runbooks", k=3)
    assert any(section.document == "lakehouse_pipeline.md" for section, _ in results)
