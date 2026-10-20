from fake_llm import ScriptedChatModel, call, final_text, report_json, tool_round
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from incident_agent import data_store
from incident_agent.config import MAX_TOOL_ROUNDS
from incident_agent.graph import IncidentAgent, initial_request, transcript

REPORT = dict(
    category="source_file_missing",
    root_cause="The inventory file arrived at 03:05, after the 02:15 run started.",
    confidence="high",
    evidence=["Run failed at 02:15 with UserErrorFileNotFound", "File arrived 03:05; SLA 02:00"],
    fix_steps=["Confirm the file is complete", "Rerun the pipeline"],
    prevention=["Agree an earlier export time with the warehouse team"],
    recommended_action="rerun",
    sources=["runbooks/source_files_and_landing.md > File not found (UserErrorFileNotFound)"],
    incident_summary="Inventory load failed because the source file arrived late. It has now arrived; rerun.",
)


def scripted(*responses):
    return ScriptedChatModel(responses=list(responses), seen=[], tool_choices=[])


def test_full_investigation_pauses_for_approval_and_reruns_when_approved():
    llm = scripted(
        tool_round(call("get_pipeline_run", run_id="run-1019-ingest-inventory")),
        tool_round(
            call("check_landing_zone", path="landing/inventory/inventory_20261019.csv"),
            call("search_knowledge", query="file not found late arrival", source="runbooks"),
        ),
        final_text("The file arrived late at 03:05; rerun."),
        report_json(**REPORT),
    )
    agent = IncidentAgent(llm)
    result = agent.diagnose(run_id="run-1019-ingest-inventory")

    assert result["tools_called"] == ["get_pipeline_run", "check_landing_zone", "search_knowledge"]
    assert result["report"]["category"] == "source_file_missing"
    assert result["budget_reached"] is False
    assert result["pending_approval"]["run_id"] == "run-1019-ingest-inventory"
    landing = next(e for e in result["trace"] if e["type"] == "tool_result" and e["tool"] == "check_landing_zone")
    assert "03:05" in landing["content"]

    # The report step gets a plain-text transcript, not raw tool messages.
    report_call = llm.seen[-1]
    assert [type(m) for m in report_call] == [SystemMessage, HumanMessage]
    assert "RESULT check_landing_zone" in report_call[1].content and "AGENT: The file arrived late" in report_call[1].content

    events = list(agent.resume(result["thread_id"], approved=True, approver="Piyush"))
    action = next(e for e in events if e["type"] == "action_result")
    assert action["approval"] == {"approved": True, "by": "Piyush"}
    assert action["result"]["status"] == "Queued"
    assert any(r["rerun_id"] == action["result"]["rerun_id"] for r in data_store.rerun_log())


def test_rejected_rerun_changes_nothing():
    before = len(data_store.rerun_log())
    agent = IncidentAgent(scripted(final_text(), report_json(**REPORT)))
    result = agent.diagnose(run_id="run-1019-ingest-inventory")
    events = list(agent.resume(result["thread_id"], approved=False, comment="Wait for the full file"))
    action = next(e for e in events if e["type"] == "action_result")
    assert action["result"]["status"] == "Rejected" and action["result"]["comment"] == "Wait for the full file"
    assert len(data_store.rerun_log()) == before


def test_no_approval_needed_when_a_fix_is_required():
    report = {**REPORT, "category": "authentication", "recommended_action": "fix_then_rerun"}
    result = IncidentAgent(scripted(final_text(), report_json(**report))).diagnose(run_id="run-1019-ingest-customers")
    assert result["report"]["recommended_action"] == "fix_then_rerun"
    assert result["pending_approval"] is None


def test_step_budget_stops_the_loop_and_still_produces_a_report():
    rounds = [tool_round(call("get_recent_runs", pipeline_name="pl_ingest_products")) for _ in range(MAX_TOOL_ROUNDS + 1)]
    llm = scripted(*rounds, report_json(**{**REPORT, "recommended_action": "escalate"}))
    result = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-products")

    assert result["budget_reached"] is True
    results = [e for e in result["trace"] if e["type"] == "tool_result"]
    assert len(results) == MAX_TOOL_ROUNDS + 1  # budget rounds + the automatic runbook search
    assert results[-1]["tool"] == "search_knowledge" and result["auto_knowledge"] is True
    assert result["report"]["recommended_action"] == "escalate"


def test_invalid_report_json_is_retried_once():
    llm = scripted(final_text(), AIMessage(content="Sure! The cause is a late file."), report_json(**REPORT))
    result = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-inventory")
    assert result["report"]["category"] == "source_file_missing"
    assert "not a valid JSON object" in llm.seen[-1][-1].content


def test_report_falls_back_to_text_when_json_never_arrives():
    llm = scripted(final_text(), AIMessage(content="no json"), AIMessage(content="still no json, the secret expired"))
    result = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-customers")
    assert result["report"]["category"] == "unknown"
    assert result["report"]["confidence"] == "low"
    assert "secret expired" in result["report"]["root_cause"]
    assert result["report"]["recommended_action"] == "escalate"  # safe default, never an automatic rerun


def test_messy_report_values_are_coerced():
    messy = {**REPORT, "category": "Schema Drift", "recommended_action": "Fix-Then-Rerun", "evidence": "one fact"}
    llm = scripted(final_text(), AIMessage(content="```json\n" + __import__("json").dumps(messy) + "\n```"))
    report = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-stores")["report"]
    assert report["category"] == "schema_drift" and report["recommended_action"] == "fix_then_rerun"
    assert report["evidence"] == ["one fact"]


def test_provider_rejecting_a_malformed_tool_call_is_retried():
    rejected = RuntimeError("Error code: 400 - {'error': {'message': 'Tool call validation failed: parameters did not match schema'}}")
    llm = scripted(
        rejected,
        tool_round(call("get_pipeline_run", run_id="run-1019-ingest-stores")),
        final_text(),
        report_json(**{**REPORT, "category": "schema_drift", "recommended_action": "fix_then_rerun"}),
    )
    result = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-stores")
    assert result["tools_called"] == ["get_pipeline_run", "search_knowledge"]  # runbooks searched automatically
    assert result["report"]["category"] == "schema_drift"


def test_other_provider_errors_are_raised():
    import pytest

    llm = scripted(RuntimeError("Error code: 401 - Invalid API Key"))
    with pytest.raises(RuntimeError, match="401"):
        IncidentAgent(llm).diagnose(run_id="run-1019-ingest-stores")


def test_tool_errors_are_returned_to_the_model_not_raised():
    llm = scripted(
        tool_round(call("get_pipeline_run", run_id="does-not-exist"), call("get_job_metrics", job_name="nb_unknown")),
        final_text(),
        report_json(**{**REPORT, "category": "unknown", "recommended_action": "escalate"}),
    )
    result = IncidentAgent(llm).diagnose(run_id="does-not-exist")
    results = [e["content"] for e in result["trace"] if e["type"] == "tool_result"]
    assert "No pipeline run with id" in results[0] and "Known:" in results[1]
    assert result["pending_approval"] is None


def test_free_text_errors_are_supported():
    assert "Investigate this pipeline failure" in initial_request(error_text="Java heap space")
    result = IncidentAgent(scripted(final_text(), report_json(**REPORT))).diagnose(error_text="Path not found: landing/inventory/x.csv")
    assert result["report"]["recommended_action"] == "rerun"
    assert result["pending_approval"] is None  # no run id, so nothing to rerun


def test_transcript_format():
    text = transcript([HumanMessage("Investigate run x"), tool_round(call("get_pipeline_run", run_id="x")), AIMessage(content="Done")])
    assert text.splitlines() == ["REQUEST: Investigate run x", 'CALL get_pipeline_run({"run_id": "x"})', "AGENT: Done"]


def test_qwen_judge_requests_fit_the_free_tier_output_limit():
    from incident_agent.llm import make_chat_model

    judge = make_chat_model("groq", "qwen/qwen3.8-27b", api_key="k", max_tokens=300)
    assert judge.max_tokens == 300 and judge.reasoning_format == "hidden"


def test_runbooks_are_searched_automatically_only_when_the_agent_skipped_them():
    skipped = scripted(
        tool_round(call("get_pipeline_run", run_id="run-1019-ingest-inventory")),
        final_text("UserErrorFileNotFound: the inventory file arrived late."),
        report_json(**REPORT),
    )
    result = IncidentAgent(skipped).diagnose(run_id="run-1019-ingest-inventory")
    auto = [e for e in result["trace"] if e["type"] == "tool_call" and e.get("auto")]
    assert result["auto_knowledge"] is True and auto[0]["args"]["query"].startswith("UserErrorFileNotFound")
    assert "RESULT search_knowledge" in skipped.seen[-1][1].content  # the report step sees the runbook text

    searched = scripted(
        tool_round(call("search_knowledge", query="file not found late")),
        final_text(),
        report_json(**REPORT),
    )
    result = IncidentAgent(searched).diagnose(run_id="run-1019-ingest-inventory")
    assert result["auto_knowledge"] is False and result["tools_called"] == ["search_knowledge"]
