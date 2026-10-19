from fake_llm import ScriptedChatModel, call, tool_round
from langchain_core.messages import AIMessage, ToolMessage

from incident_agent import data_store
from incident_agent.config import MAX_TOOL_ROUNDS
from incident_agent.graph import IncidentAgent, initial_request

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
        tool_round(call("submit_diagnosis", **REPORT)),
    )
    agent = IncidentAgent(llm)
    result = agent.diagnose(run_id="run-1019-ingest-inventory")

    assert result["tools_called"] == ["get_pipeline_run", "check_landing_zone", "search_knowledge"]
    assert result["report"]["category"] == "source_file_missing"
    assert result["pending_approval"]["run_id"] == "run-1019-ingest-inventory"
    landing = next(e for e in result["trace"] if e["type"] == "tool_result" and e["tool"] == "check_landing_zone")
    assert "03:05" in landing["content"]

    events = list(agent.resume(result["thread_id"], approved=True, approver="Piyush"))
    action = next(e for e in events if e["type"] == "action_result")
    assert action["approval"] == {"approved": True, "by": "Piyush"}
    assert action["result"]["status"] == "Queued"
    assert any(r["rerun_id"] == action["result"]["rerun_id"] for r in data_store.rerun_log())


def test_rejected_rerun_changes_nothing():
    before = len(data_store.rerun_log())
    agent = IncidentAgent(scripted(tool_round(call("submit_diagnosis", **REPORT))))
    result = agent.diagnose(run_id="run-1019-ingest-inventory")
    events = list(agent.resume(result["thread_id"], approved=False, comment="Wait for the full file"))
    action = next(e for e in events if e["type"] == "action_result")
    assert action["result"]["status"] == "Rejected" and action["result"]["comment"] == "Wait for the full file"
    assert len(data_store.rerun_log()) == before


def test_no_approval_needed_when_a_fix_is_required():
    report = {**REPORT, "category": "authentication", "recommended_action": "fix_then_rerun"}
    result = IncidentAgent(scripted(tool_round(call("submit_diagnosis", **report)))).diagnose(run_id="run-1019-ingest-customers")
    assert result["report"]["recommended_action"] == "fix_then_rerun"
    assert result["pending_approval"] is None


def test_budget_limit_forces_a_diagnosis():
    rounds = [tool_round(call("get_recent_runs", pipeline_name="pl_ingest_products")) for _ in range(MAX_TOOL_ROUNDS + 1)]
    llm = scripted(*rounds, tool_round(call("submit_diagnosis", **{**REPORT, "recommended_action": "escalate"})))
    result = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-products")

    assert result["tools_called"].count("get_recent_runs") == MAX_TOOL_ROUNDS + 1  # last round requested, not run
    assert sum(1 for e in result["trace"] if e["type"] == "tool_result") == MAX_TOOL_ROUNDS
    assert result["forced_submit"] is True
    assert llm.tool_choices[-1] == "submit_diagnosis"
    # The skipped call was answered before the model was asked again (provider APIs require it).
    final_prompt = llm.seen[-1]
    skipped = [m for m in final_prompt if isinstance(m, ToolMessage) and "budget" in str(m.content)]
    assert len(skipped) == 1


def test_plain_text_answer_is_turned_into_a_structured_report():
    llm = scripted(
        AIMessage(content="I think the secret expired."),
        tool_round(call("submit_diagnosis", category="Authentication", root_cause="Secret expired.", confidence="medium",
                        evidence=["test connection failed"], fix_steps=["Rotate secret"], recommended_action="fix then rerun",
                        incident_summary="Secret expired.")),
    )
    result = IncidentAgent(llm).diagnose(run_id="run-1019-ingest-customers")
    assert result["forced_submit"] is True
    assert result["report"]["category"] == "authentication"
    assert result["report"]["recommended_action"] == "fix_then_rerun"


def test_tool_errors_are_returned_to_the_model_not_raised():
    llm = scripted(
        tool_round(call("get_pipeline_run", run_id="does-not-exist"), call("get_job_metrics", job_name="nb_unknown")),
        tool_round(call("submit_diagnosis", **{**REPORT, "category": "unknown", "recommended_action": "escalate"})),
    )
    result = IncidentAgent(llm).diagnose(run_id="does-not-exist")
    results = [e["content"] for e in result["trace"] if e["type"] == "tool_result"]
    assert "No pipeline run with id" in results[0] and "Known:" in results[1]
    assert result["pending_approval"] is None


def test_free_text_errors_are_supported():
    assert "Investigate this pipeline failure" in initial_request(error_text="Java heap space")
    result = IncidentAgent(scripted(tool_round(call("submit_diagnosis", **{**REPORT, "recommended_action": "rerun"})))).diagnose(
        error_text="Path not found: landing/inventory/x.csv"
    )
    assert result["report"]["recommended_action"] == "rerun"
    assert result["pending_approval"] is None  # no run id, so nothing to rerun
