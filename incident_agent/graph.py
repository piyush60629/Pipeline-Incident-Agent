"""The incident agent as a LangGraph state machine (two phases: investigate, then report).

    START -> investigate --(tool calls)--> tools --> investigate        (up to MAX_TOOL_ROUNDS)
                 |
                 +--(answers without tools, or budget used)--> report
                                                                 |
                          recommended_action == "rerun" --> approval (human-in-the-loop interrupt)
                                                                 |              |
                                                                END   approved -> rerun (simulated)

The tools, instructions and rerun action come from the environment (environments.py): the synthetic
Azure platform or the real Retail Analytics Lakehouse run logs.

* investigate: the LLM with read-only tools decides what to check next (ReAct loop).
* tools: LangGraph's ToolNode runs the requested tools (several in parallel per round).
* knowledge: if the agent never searched the runbooks, one automatic search with its summary.
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

from .config import MAX_TOOL_ROUNDS
from .environments import DEFAULT_ENVIRONMENT, ENVIRONMENTS, get_environment
from .schemas import coerce_report, extract_json_object, report_field_guide
from .tools import search_knowledge

SYSTEM_PROMPT = ENVIRONMENTS["azure"].system_prompt  # kept for backwards compatibility

REPORT_PROMPT = f"""You turn an incident investigation into a JSON report. Use only facts that appear in the
investigation transcript. Return ONLY one JSON object with these fields:
{report_field_guide()}

Rules for recommended_action: 'rerun' only when nothing needs changing (transient error, or the cause is
already resolved, such as a late file that has now arrived); 'fix_then_rerun' when the data team can fix it
first (including correcting a bad source file or rerunning from the correct stage); 'escalate' when only another
team can fix or approve it (IAM role assignments, Snowflake quotas, source data owners deciding a rule)."""

INVALID_CALL_NUDGE = "Your last tool call was rejected by the API ({error}). Call the tools again with valid arguments, or summarise your findings."


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    run_id: str | None
    tool_rounds: int
    budget_reached: bool
    auto_knowledge: bool
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


def knowledge_query(messages: list[AnyMessage]) -> str:
    """Search words for an automatic runbook lookup: the agent's summary, else the original request."""

    for message in reversed(messages):
        if isinstance(message, AIMessage) and _text(message.content).strip():
            return _text(message.content).strip()[:400]
    request = next((m for m in messages if isinstance(m, HumanMessage)), None)
    return _text(request.content)[:400] if request else "pipeline failure"


def _is_bad_tool_call(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("tool call validation failed", "parsing failed", "failed to call a function", "tool_use_failed"))


def build_graph(llm: BaseChatModel, checkpointer: Any = None, environment: str = DEFAULT_ENVIRONMENT):
    """Compile the agent graph around any LangChain chat model that supports tool calling."""

    env = get_environment(environment)
    investigator = llm.bind_tools(env.tools)
    tool_node = ToolNode(env.tools, handle_tool_errors=True)

    def investigate(state: AgentState) -> dict:
        messages = [SystemMessage(env.system_prompt), *state["messages"]]
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
        run = env.get_run(state["run_id"])
        decision = interrupt(
            {
                "kind": "rerun_approval",
                "environment": env.key,
                "run_id": run["run_id"],
                "pipeline": run["pipeline"],
                "question": env.rerun_question(run),
                "reason": report["root_cause"],
            }
        )
        decision = decision if isinstance(decision, dict) else {"approved": bool(decision)}
        approver = str(decision.get("approver") or "on-call engineer")
        if decision.get("approved"):
            result = env.trigger_rerun(run["run_id"], approved_by=approver, reason=report["root_cause"])
        else:
            result = {"status": "Rejected", "rejected_by": approver, "comment": str(decision.get("comment", ""))}
        return {"approval": {"approved": bool(decision.get("approved")), "by": approver}, "action_result": result}

    def after_investigate(state: AgentState) -> str:
        if _pending_tool_calls(state["messages"][-1]) and state.get("tool_rounds", 0) < MAX_TOOL_ROUNDS:
            return "tools"
        return "report"

    def mark_budget(state: AgentState) -> dict:
        return {"budget_reached": bool(_pending_tool_calls(state["messages"][-1]))}

    def ensure_knowledge(state: AgentState) -> dict:
        """If the agent never searched the runbooks, look them up once before the report, so the fix
        steps follow the team's procedure. (In evaluation the model often skipped this step.)"""

        searched = any(isinstance(m, ToolMessage) and m.name == "search_knowledge" for m in state["messages"])
        if searched or search_knowledge not in env.tools:
            return {"auto_knowledge": False}
        query = knowledge_query(state["messages"])
        call_id = f"auto_{uuid.uuid4().hex[:8]}"
        call = {"name": "search_knowledge", "args": {"query": query}, "id": call_id, "type": "tool_call"}
        result = search_knowledge.invoke({"query": query})
        return {
            "messages": [AIMessage(content="", tool_calls=[call]),
                         ToolMessage(content=result, name="search_knowledge", tool_call_id=call_id)],
            "auto_knowledge": True,
        }

    def after_report(state: AgentState) -> str:
        report = state.get("report") or {}
        if report.get("recommended_action") == "rerun" and state.get("run_id"):
            try:
                env.get_run(state["run_id"])
            except env.not_found:
                return END
            return "approval"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("investigate", investigate)
    graph.add_node("tools", run_tools)
    graph.add_node("check_budget", mark_budget)
    graph.add_node("knowledge", ensure_knowledge)
    graph.add_node("report", report)
    graph.add_node("approval", approval)
    graph.add_edge(START, "investigate")
    graph.add_conditional_edges("investigate", after_investigate, {"tools": "tools", "report": "check_budget"})
    graph.add_edge("tools", "investigate")
    graph.add_edge("check_budget", "knowledge")
    graph.add_edge("knowledge", "report")
    graph.add_conditional_edges("report", after_report, ["approval", END])
    graph.add_edge("approval", END)
    return graph.compile(checkpointer=checkpointer or InMemorySaver())


# --------------------------------------------------------------------------- #
# A small wrapper that turns graph updates into simple events for the UI, CLI and evaluation
# --------------------------------------------------------------------------- #


class IncidentAgent:
    """One agent that can investigate any environment; a graph is compiled per environment on first use."""

    def __init__(self, llm: BaseChatModel, checkpointer: Any = None, environment: str = DEFAULT_ENVIRONMENT) -> None:
        self.llm = llm
        self.checkpointer = checkpointer or InMemorySaver()
        self.default_environment = get_environment(environment).key
        self._graphs: dict[str, Any] = {}
        self._thread_environment: dict[str, str] = {}

    def graph_for(self, environment: str | None = None):
        key = get_environment(environment or self.default_environment).key
        if key not in self._graphs:
            self._graphs[key] = build_graph(self.llm, self.checkpointer, key)
        return self._graphs[key]

    @property
    def graph(self):
        return self.graph_for(self.default_environment)

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
                            elif node == "knowledge":
                                yield {"type": "tool_call", "tool": call["name"], "args": call["args"], "auto": True}
                    elif isinstance(message, ToolMessage) and node in ("tools", "knowledge"):
                        yield {"type": "tool_result", "tool": message.name, "content": str(message.content)}
                if node == "check_budget" and payload.get("budget_reached"):
                    yield {"type": "budget_reached"}
                if payload.get("report"):
                    yield {"type": "report", "report": payload["report"]}
                if payload.get("action_result"):
                    yield {"type": "action_result", "approval": payload.get("approval"), "result": payload["action_result"]}

    def stream(self, run_id: str | None = None, error_text: str | None = None, thread_id: str | None = None,
               environment: str | None = None) -> Iterator[dict]:
        key = get_environment(environment or self.default_environment).key
        graph = self.graph_for(key)
        thread_id = thread_id or uuid.uuid4().hex
        self._thread_environment[thread_id] = key
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
        yield {"type": "start", "thread_id": thread_id, "run_id": run_id, "environment": key}
        state = {"messages": [HumanMessage(initial_request(run_id, error_text))], "run_id": run_id, "tool_rounds": 0}
        yield from self._events(graph.stream(state, config, stream_mode="updates"))

    def resume(self, thread_id: str, approved: bool, approver: str = "on-call engineer", comment: str = "") -> Iterator[dict]:
        graph = self.graph_for(self._thread_environment.get(thread_id, self.default_environment))
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 40}
        command = Command(resume={"approved": approved, "approver": approver, "comment": comment})
        yield from self._events(graph.stream(command, config, stream_mode="updates"))

    def diagnose(self, run_id: str | None = None, error_text: str | None = None, environment: str | None = None) -> dict:
        """Run to completion (or to the approval pause) and return a summary of the run."""

        started = time.perf_counter()
        trace, report, pending, thread_id, budget, auto = [], None, None, None, False, False
        for event in self.stream(run_id=run_id, error_text=error_text, environment=environment):
            if event["type"] == "start":
                thread_id = event["thread_id"]
            elif event["type"] in ("tool_call", "tool_result"):
                trace.append(event)
                auto = auto or bool(event.get("auto"))
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
            "auto_knowledge": auto,
            "seconds": round(time.perf_counter() - started, 1),
        }
