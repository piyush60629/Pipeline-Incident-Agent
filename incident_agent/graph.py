"""The incident agent as a LangGraph state machine (two phases: investigate, then report).

    START -> investigate --(tool calls)--> tools --> investigate        (up to MAX_TOOL_ROUNDS)
                 |
                 +--(answers without tools, or budget used)--> report
                                                                 |
                          recommended_action == "rerun" --> approval (human-in-the-loop interrupt)
                                                                 |              |
                                                                END   approved -> rerun (simulated)

* investigate: the LLM with seven read-only tools decides what to check next (ReAct loop).
* tools: LangGraph's ToolNode runs the requested tools (several in parallel per round).
* report: a separate LLM call turns the investigation transcript into a JSON DiagnosisReport,
  validated with Pydantic, with one retry if the JSON is invalid.
* approval: pauses the graph with interrupt() until a person approves or rejects the rerun.
  The LLM never has a tool that changes anything; only this approved node can.

Why two phases: in evaluation, gpt-oss on Groq almost never ended by calling a "submit report"
tool, and forcing that call produced provider-side 400 errors (strict tool-call validation of a
large schema). Letting the model investigate freely and then extracting the report as JSON
removed those failures.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Annotated, Any, Iterator, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langgraph.types import Command, interrupt

from . import data_store
from .config import MAX_TOOL_ROUNDS
from .schemas import coerce_report, extract_json_object, report_field_guide
from .tools import INVESTIGATION_TOOLS

SYSTEM_PROMPT = f"""You are an on-call data platform engineer investigating a failed pipeline run on an Azure
Data Factory + ADLS Gen2 + Databricks + Snowflake platform.

How to work:
1. If you have a run id, call get_pipeline_run first.
2. Gather evidence with the specific tools for the error (several tools per round is fine):
   credentials, permissions, runtimes or quotas -> check_linked_service; column or data quality errors ->
   compare_source_schema; timeouts or out-of-memory -> get_job_metrics; missing, late or duplicate
   files -> check_landing_zone and get_recent_runs; transient-looking errors -> get_recent_runs and
   check_linked_service; upstream dependency failures -> get_pipeline_run on the upstream run id.
3. Call search_knowledge at least once with the key error words and your suspected cause, to find the
   team's runbook fix and similar past incidents.
4. Only state facts you saw in tool results. Use at most {MAX_TOOL_ROUNDS} tool rounds.
5. When you have enough evidence, stop calling tools and reply with a short summary of the root cause,
   the key evidence, the fix and whether a rerun, a fix first, or another team is needed."""

REPORT_PROMPT = f"""You turn an incident investigation into a JSON report. Use only facts that appear in the
investigation transcript. Return ONLY one JSON object with these fields:
{report_field_guide()}

Rules for recommended_action: 'rerun' only when nothing needs changing (transient error, or the cause is
already resolved, such as a late file that has now arrived); 'fix_then_rerun' when the data team can fix it
first; 'escalate' when only another team can fix or approve it (IAM role assignments, Snowflake quotas,
source data owners deciding a rule)."""

INVALID_CALL_NUDGE = "Your last tool call was rejected by the API ({error}). Call the tools again with valid arguments, or summarise your findings."


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    run_id: str | None
    tool_rounds: int
    budget_reached: bool
    report: dict | None
    report_attempts: int
    approval: dict | None
    action_result: dict | None


def _pending_tool_calls(message: AnyMessage) -> list[dict]:
    return list(getattr(message, "tool_calls", None) or [])


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # content blocks
        return " ".join(str(block.get("text", "")) if isinstance(block, dict) else str(block) for block in content)
    return str(content or "")


def initial_request(run_id: str | None = None, error_text: str | None = None) -> str:
    if run_id:
        text = f"Investigate failed pipeline run `{run_id}` and diagnose the root cause."
        if error_text:
            text += f"\nExtra context from the engineer:\n{error_text}"
        return text
    if error_text:
        return f"Investigate this pipeline failure and diagnose the root cause:\n{error_text}"
    raise ValueError("Provide a run_id or an error message.")


def transcript(messages: list[AnyMessage]) -> str:
    """Plain-text record of the investigation, so the report step needs no tool definitions."""

    lines = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append(f"REQUEST: {_text(message.content)}")
        elif isinstance(message, AIMessage):
            if _text(message.content).strip():
                lines.append(f"AGENT: {_text(message.content).strip()}")
            for call in _pending_tool_calls(message):
                lines.append(f"CALL {call['name']}({json.dumps(call['args'])})")
        elif isinstance(message, ToolMessage):
            lines.append(f"RESULT {message.name}: {_text(message.content)}")
    return "\n".join(lines)


def _is_bad_tool_call(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("tool call validation failed", "parsing failed", "failed to call a function", "tool_use_failed"))


def build_graph(llm: BaseChatModel, checkpointer: Any = None):
    """Compile the agent graph around any LangChain chat model that supports tool calling."""

    investigator = llm.bind_tools(INVESTIGATION_TOOLS)
    tool_node = ToolNode(INVESTIGATION_TOOLS, handle_tool_errors=True)

    def investigate(state: AgentState) -> dict:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        try:
            response = investigator.invoke(messages)
        except Exception as exc:  # noqa: BLE001 - provider rejected a malformed tool call: retry once
            if not _is_bad_tool_call(exc):
                raise
            nudge = HumanMessage(INVALID_CALL_NUDGE.format(error=str(exc)[:200]))
            try:
                response = investigator.invoke([*messages, nudge])
            except Exception as retry_exc:  # noqa: BLE001
                if not _is_bad_tool_call(retry_exc):
                    raise
                response = AIMessage(content="Tool calls failed twice; reporting with the evidence gathered so far.")
            return {"messages": [nudge, response]}
        return {"messages": [response]}

    def run_tools(state: AgentState) -> dict:
        result = tool_node.invoke({"messages": [state["messages"][-1]]})
        return {"messages": result["messages"], "tool_rounds": state.get("tool_rounds", 0) + 1}

    def report(state: AgentState) -> dict:
        record = transcript(state["messages"])
        request = [SystemMessage(REPORT_PROMPT), HumanMessage(f"Investigation transcript:\n{record}\n\nReturn the JSON report now.")]
        reply = llm.invoke(request)
        parsed = extract_json_object(_text(reply.content))
        attempts = 1
        if parsed is None:  # one retry with a reminder
            attempts = 2
            reply = llm.invoke([*request, reply, HumanMessage("That was not a valid JSON object. Return only the JSON object.")])
            parsed = extract_json_object(_text(reply.content))
        report = coerce_report(parsed if parsed is not None else {"root_cause": _text(reply.content)[:500], "confidence": "low"})
        return {"report": report.model_dump(), "report_attempts": attempts}

    def approval(state: AgentState) -> dict:
        report = state["report"]
        run = data_store.get_run(state["run_id"])
        decision = interrupt(
            {
                "kind": "rerun_approval",
                "run_id": run["run_id"],
                "pipeline": run["pipeline"],
                "question": f"Rerun pipeline '{run['pipeline']}' (run {run['run_id']})?",
                "reason": report["root_cause"],
            }
        )
        decision = decision if isinstance(decision, dict) else {"approved": bool(decision)}
        approver = str(decision.get("approver") or "on-call engineer")
        if decision.get("approved"):
            result = data_store.trigger_rerun(run["run_id"], approved_by=approver, reason=report["root_cause"])
        else:
            result = {"status": "Rejected", "rejected_by": approver, "comment": str(decision.get("comment", ""))}
        return {"approval": {"approved": bool(decision.get("approved")), "by": approver}, "action_result": result}

    def after_investigate(state: AgentState) -> str:
        if _pending_tool_calls(state["messages"][-1]) and state.get("tool_rounds", 0) < MAX_TOOL_ROUNDS:
            return "tools"
        return "report"

    def mark_budget(state: AgentState) -> dict:
        return {"budget_reached": bool(_pending_tool_calls(state["messages"][-1]))}

    def after_report(state: AgentState) -> str:
        report = state.get("report") or {}
        if report.get("recommended_action") == "rerun" and state.get("run_id"):
            try:
                data_store.get_run(state["run_id"])
            except data_store.NotFound:
                return END
            return "approval"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("investigate", investigate)
    graph.add_node("tools", run_tools)
    graph.add_node("check_budget", mark_budget)
    graph.add_node("report", report)
    graph.add_node("approval", approval)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges("investigate", after_investigate, {"tools": "tools", "report": "check_budget"})
    graph.add_edge("tools", "investigate")
    graph.add_edge("check_budget", "report")
    graph.add_conditional_edges("report", after_report, ["approval", END])
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# --------------------------------------------------------------------------- #
# A small wrapper that turns graph updates into simple events for the UI, CLI and evaluation
# --------------------------------------------------------------------------- #


class IncidentAgent:
    def __init__(self, llm: BaseChatModel, checkpointer: Any = None) -> None:
        self.graph = build_graph(llm, checkpointer)

    def _events(self, stream: Iterator[dict]) -> Iterator[dict]:
        for update in stream:
            for node, payload in update.items():
                if node == "__interrupt__":
                    for item in payload:
                        yield {**item.value, "type": "approval_required"}
                    continue
                payload = payload or {}
                for message in payload.get("messages", []):
                    if isinstance(message, AIMessage):
                        for call in _pending_tool_calls(message):
                            if node == "investigate":
                                yield {"type": "tool_call", "tool": call["name"], "args": call["args"]}
                    elif isinstance(message, ToolMessage) and node == "tools":
                        yield {"type": "tool_result", "tool": message.name, "content": str(message.content)}
                if node == "check_budget" and payload.get("budget_reached"):
                    yield {"type": "budget_reached"}
                if payload.get("report"):
                    yield {"type": "report", "report": payload["report"]}
                if payload.get("action_result"):
                    yield {"type": "action_result", "approval": payload.get("approval"), "result": payload["action_result"]}

    def stream(self, run_id: str | None = None, error_text: str | None = None, thread_id: str | None = None) -> Iterator[dict]:
        thread_id = thread_id or uuid.uuid4().hex
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
        yield {"type": "start", "thread_id": thread_id, "run_id": run_id}
        state = {"messages": [HumanMessage(initial_request(run_id, error_text))], "run_id": run_id, "tool_rounds": 0}
        yield from self._events(self.graph.stream(state, config, stream_mode="updates"))

    def resume(self, thread_id: str, approved: bool, approver: str = "on-call engineer", comment: str = "") -> Iterator[dict]:
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
        command = Command(resume={"approved": approved, "approver": approver, "comment": comment})
        yield from self._events(self.graph.stream(command, config, stream_mode="updates"))

    def diagnose(self, run_id: str | None = None, error_text: str | None = None) -> dict:
        """Run to completion (or to the approval pause) and return a summary of the run."""

        started = time.perf_counter()
        trace, report, pending, thread_id, budget = [], None, None, None, False
        for event in self.stream(run_id=run_id, error_text=error_text):
            if event["type"] == "start":
                thread_id = event["thread_id"]
            elif event["type"] in ("tool_call", "tool_result"):
                trace.append(event)
            elif event["type"] == "report":
                report = event["report"]
            elif event["type"] == "approval_required":
                pending = event
            elif event["type"] == "budget_reached":
                budget = True
        return {
            "thread_id": thread_id,
            "report": report,
            "pending_approval": pending,
            "tools_called": [e["tool"] for e in trace if e["type"] == "tool_call"],
            "trace": trace,
            "budget_reached": budget,
            "seconds": round(time.perf_counter() - started, 1),
        }
