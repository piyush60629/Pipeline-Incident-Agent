"""The incident agent as a LangGraph state machine.

    START -> investigate --(tool calls)--> tools --> investigate      (up to MAX_TOOL_ROUNDS)
                 |  \\--(no tool call / budget used)--> force_submit
                 |                                         |
                 \\--(submit_diagnosis)--> finalize <-------/
                                              |
                     recommended_action == "rerun" -> approval (human-in-the-loop interrupt)
                                              |                        |
                                             END        approved -> rerun (simulated) -> END

* investigate: the LLM with the read-only tools bound decides what to check next.
* tools: LangGraph's ToolNode runs the requested tools (several in parallel per round).
* force_submit: if the model stops calling tools without a diagnosis, or uses up its
  budget, it is made to call submit_diagnosis with the evidence it has.
* finalize: validates the diagnosis into a DiagnosisReport.
* approval: pauses the graph with interrupt() until a person approves or rejects the rerun.
  The LLM never has a tool that changes anything; only this approved node can.
"""

from __future__ import annotations

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
from .schemas import coerce_report
from .tools import INVESTIGATION_TOOLS, submit_diagnosis

SYSTEM_PROMPT = f"""You are an on-call data platform engineer investigating a failed pipeline run on an Azure
Data Factory + ADLS Gen2 + Databricks + Snowflake platform.

How to work:
1. If you have a run id, call get_pipeline_run first.
2. Gather evidence with the specific tools for the error (several tools per round is fine):
   credentials, permissions, runtimes or quotas -> check_linked_service; column or data quality errors ->
   compare_source_schema; timeouts or out-of-memory -> get_job_metrics; missing, late or duplicate
   files -> check_landing_zone and get_recent_runs; transient-looking errors -> get_recent_runs and
   check_linked_service; upstream dependency failures -> get_pipeline_run on the upstream run id.
3. Before submitting, call search_knowledge at least once with the key error words and your suspected
   cause, so the fix steps follow the team's runbook and cite it in sources.
4. Only state facts you saw in tool results. Use at most {MAX_TOOL_ROUNDS} tool rounds.
5. Your final step must be a submit_diagnosis tool call. Never write the diagnosis as plain text.
   Choose recommended_action carefully: 'rerun' only when nothing needs changing (transient error, or the
   cause is already resolved); 'fix_then_rerun' when the data team can fix it first; 'escalate' when only
   another team can fix or approve it (IAM roles, quotas, source data owners)."""

FORCE_SUBMIT_PROMPT = (
    "Stop investigating now. Call submit_diagnosis with your best diagnosis based only on the evidence "
    "gathered so far. Lower the confidence if evidence is missing."
)


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    run_id: str | None
    tool_rounds: int
    report: dict | None
    approval: dict | None
    action_result: dict | None


def _pending_tool_calls(message: AnyMessage) -> list[dict]:
    return list(getattr(message, "tool_calls", None) or [])


def initial_request(run_id: str | None = None, error_text: str | None = None) -> str:
    if run_id:
        text = f"Investigate failed pipeline run `{run_id}` and diagnose the root cause."
        if error_text:
            text += f"\nExtra context from the engineer:\n{error_text}"
        return text
    if error_text:
        return f"Investigate this pipeline failure and diagnose the root cause:\n{error_text}"
    raise ValueError("Provide a run_id or an error message.")


def build_graph(llm: BaseChatModel, checkpointer: Any = None):
    """Compile the agent graph around any LangChain chat model that supports tool calling."""

    investigator = llm.bind_tools([*INVESTIGATION_TOOLS, submit_diagnosis])
    submitter = llm.bind_tools([submit_diagnosis], tool_choice="submit_diagnosis")
    tool_node = ToolNode(INVESTIGATION_TOOLS, handle_tool_errors=True)

    def investigate(state: AgentState) -> dict:
        response = investigator.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])
        return {"messages": [response]}

    def run_tools(state: AgentState) -> dict:
        last = state["messages"][-1]
        # submit_diagnosis is handled by the graph, not executed as a tool.
        calls = [c for c in _pending_tool_calls(last) if c["name"] != "submit_diagnosis"]
        result = tool_node.invoke({"messages": [AIMessage(content=last.content, tool_calls=calls, id=last.id)]})
        return {"messages": result["messages"], "tool_rounds": state.get("tool_rounds", 0) + 1}

    def force_submit(state: AgentState) -> dict:
        last = state["messages"][-1]
        # Every tool call needs an answer before the model can be called again.
        skipped = [
            ToolMessage(content="Not run: investigation budget used up.", tool_call_id=c["id"], name=c["name"])
            for c in _pending_tool_calls(last)
        ]
        prompt = HumanMessage(FORCE_SUBMIT_PROMPT)
        response = submitter.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"], *skipped, prompt])
        return {"messages": [*skipped, prompt, response]}

    def finalize(state: AgentState) -> dict:
        last = state["messages"][-1]
        call = next((c for c in _pending_tool_calls(last) if c["name"] == "submit_diagnosis"), None)
        report = coerce_report(call["args"] if call else {"root_cause": str(last.content or "")})
        return {"report": report.model_dump()}

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
        calls = _pending_tool_calls(state["messages"][-1])
        if any(c["name"] == "submit_diagnosis" for c in calls):
            return "finalize"
        if calls and state.get("tool_rounds", 0) < MAX_TOOL_ROUNDS:
            return "tools"
        return "force_submit"

    def after_finalize(state: AgentState) -> str:
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
    graph.add_node("force_submit", force_submit)
    graph.add_node("finalize", finalize)
    graph.add_node("approval", approval)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges("investigate", after_investigate, ["tools", "finalize", "force_submit"])
    graph.add_edge("tools", "investigate")
    graph.add_edge("force_submit", "finalize")
    graph.add_conditional_edges("finalize", after_finalize, ["approval", END])
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
                            if call["name"] != "submit_diagnosis":
                                yield {"type": "tool_call", "tool": call["name"], "args": call["args"]}
                    elif isinstance(message, ToolMessage) and node == "tools":
                        yield {"type": "tool_result", "tool": message.name, "content": str(message.content)}
                if node == "force_submit":
                    yield {"type": "forced_submit"}
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
        trace, report, pending, thread_id, forced = [], None, None, None, False
        for event in self.stream(run_id=run_id, error_text=error_text):
            if event["type"] == "start":
                thread_id = event["thread_id"]
            elif event["type"] in ("tool_call", "tool_result"):
                trace.append(event)
            elif event["type"] == "report":
                report = event["report"]
            elif event["type"] == "approval_required":
                pending = event
            elif event["type"] == "forced_submit":
                forced = True
        return {
            "thread_id": thread_id,
            "report": report,
            "pending_approval": pending,
            "tools_called": [e["tool"] for e in trace if e["type"] == "tool_call"],
            "trace": trace,
            "forced_submit": forced,
            "seconds": round(time.perf_counter() - started, 1),
        }
