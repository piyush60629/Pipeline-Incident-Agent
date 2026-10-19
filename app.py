"""Streamlit UI for the Pipeline Incident Agent."""

from __future__ import annotations

import json
import os

import streamlit as st

APP_TITLE = "Pipeline Incident Agent"
ACTION_LABELS = {
    "rerun": "Rerun (needs approval)",
    "fix_then_rerun": "Fix, then rerun",
    "escalate": "Escalate to another team",
    "no_action": "No action",
}
TOOL_LABELS = {
    "get_pipeline_run": "Read the run details",
    "get_recent_runs": "Check recent run history",
    "check_linked_service": "Check the connection",
    "compare_source_schema": "Compare source schema",
    "get_job_metrics": "Read Databricks job metrics",
    "check_landing_zone": "Check landing zone files",
    "search_knowledge": "Search runbooks and incidents",
}


def load_secrets() -> None:
    """Copy Streamlit secrets into environment variables, which the agent reads."""

    for name in ("GROQ_API_KEY", "GEMINI_API_KEY", "LLM_PROVIDER", "APP_PASSWORD"):
        try:
            value = st.secrets.get(name)
        except Exception:  # noqa: BLE001 - no secrets file locally
            value = None
        if value and not os.getenv(name):
            os.environ[name] = str(value)


def require_optional_password() -> None:
    expected = os.getenv("APP_PASSWORD", "")
    if not expected or st.session_state.get("authenticated"):
        return
    st.title(APP_TITLE)
    supplied = st.text_input("Demo password", type="password")
    if st.button("Open demo", use_container_width=True):
        if supplied == expected:
            st.session_state.authenticated = True
            st.rerun()
        st.error("Incorrect password.")
    st.stop()


@st.cache_resource(show_spinner=False)
def get_agent(provider: str, model: str):
    from incident_agent import create_agent

    return create_agent(provider=provider, model=model)


def model_settings() -> tuple[str, str] | None:
    from incident_agent.config import default_agent_model, resolve_provider

    try:
        provider = resolve_provider(os.getenv("LLM_PROVIDER") or None)
    except ValueError:
        return None
    key = os.getenv("GROQ_API_KEY") if provider == "groq" else os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    return provider, default_agent_model(provider)


def sidebar(settings: tuple[str, str] | None) -> None:
    from incident_agent.knowledge import default_knowledge_base

    with st.sidebar:
        st.header("How it works")
        if settings:
            st.success(f"Agent model: {settings[1]} via {settings[0].title()}")
        else:
            st.error("Add GROQ_API_KEY (or GEMINI_API_KEY) in the app secrets.")
        st.caption(f"Knowledge search: {default_knowledge_base().mode}")
        st.markdown(
            "1. **Investigate**: the LLM picks tools to call (run details, history, connections, "
            "schemas, job metrics, landing files, runbooks).\n"
            "2. **Loop**: it reads the results and decides what to check next (up to 6 rounds).\n"
            "3. **Diagnose**: it submits a structured report with evidence and fix steps.\n"
            "4. **Approve**: a rerun only happens after a person approves it."
        )
        st.caption("Built with LangGraph, LangChain, Groq and Streamlit. All pipeline data is synthetic.")


def show_event(event: dict) -> None:
    if event["type"] == "tool_call":
        label = TOOL_LABELS.get(event["tool"], event["tool"])
        st.markdown(f"**{label}** · `{event['tool']}({json.dumps(event['args'])})`")
    elif event["type"] == "tool_result":
        with st.expander(f"Result of {event['tool']}", expanded=False):
            try:
                st.json(json.loads(event["content"]))
            except (json.JSONDecodeError, TypeError):
                st.code(event["content"])
    elif event["type"] == "forced_submit":
        st.caption("Investigation budget reached; asking the agent for its diagnosis.")


def show_report(report: dict) -> None:
    col1, col2, col3 = st.columns(3)
    col1.metric("Category", report["category"].replace("_", " "))
    col2.metric("Confidence", report["confidence"])
    col3.metric("Recommended action", ACTION_LABELS.get(report["recommended_action"], report["recommended_action"]))
    st.info(f"**Root cause:** {report['root_cause']}")

    left, right = st.columns(2)
    with left:
        st.markdown("**Evidence**")
        st.markdown("\n".join(f"- {item}" for item in report["evidence"]) or "_None given_")
        if report["prevention"]:
            st.markdown("**Prevention**")
            st.markdown("\n".join(f"- {item}" for item in report["prevention"]))
    with right:
        st.markdown("**Fix steps**")
        st.markdown("\n".join(f"{n}. {item}" for n, item in enumerate(report["fix_steps"], start=1)) or "_None given_")
        if report["sources"]:
            st.markdown("**Sources**")
            st.markdown("\n".join(f"- `{item}`" for item in report["sources"]))
    st.markdown("**Ticket summary** (copy into the incident ticket)")
    st.code(report["incident_summary"], language=None, wrap_lines=True)


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🛠️", layout="wide")
    load_secrets()
    require_optional_password()

    from incident_agent import data_store

    st.title(APP_TITLE)
    st.caption(
        "An AI agent that investigates failed data pipeline runs: it chooses which systems to check, "
        "reads the evidence, finds the runbook fix and asks before rerunning anything."
    )
    settings = model_settings()
    sidebar(settings)

    failures = data_store.failed_runs()
    mode = st.radio("What failed?", ["Pick a failed run", "Paste an error message"], horizontal=True)
    run_id, error_text = None, None
    if mode == "Pick a failed run":
        options = {f"{r['pipeline']}  ·  {r['error_code']}  ·  {r['start'][:16].replace('T', ' ')}": r for r in failures}
        choice = st.selectbox(f"{len(failures)} open failures (synthetic data)", list(options))
        run = options[choice]
        run_id = run["run_id"]
        st.code(run["error_message"], language=None, wrap_lines=True)
    else:
        error_text = st.text_area(
            "Error message",
            placeholder="Paste an ADF / Databricks / Snowflake error, e.g. 'java.lang.OutOfMemoryError: Java heap space in nb_silver_sales_join'",
            height=120,
        )

    streamed_now = False
    if st.button("Diagnose", type="primary", disabled=settings is None, use_container_width=True):
        if not run_id and not (error_text or "").strip():
            st.warning("Paste an error message first.")
        else:
            agent = get_agent(*settings)
            session = {"events": [], "report": None, "pending": None, "action": None, "thread_id": None, "error": None}
            st.session_state.diagnosis = session
            streamed_now = True
            with st.status("Investigating…", expanded=True) as status:
                try:
                    for event in agent.stream(run_id=run_id, error_text=error_text):
                        if event["type"] == "start":
                            session["thread_id"] = event["thread_id"]
                            continue
                        session["events"].append(event)
                        if event["type"] == "report":
                            session["report"] = event["report"]
                        elif event["type"] == "approval_required":
                            session["pending"] = event
                        else:
                            show_event(event)
                    status.update(label="Diagnosis ready", state="complete", expanded=False)
                except Exception as exc:  # noqa: BLE001 - show provider errors to the user
                    session["error"] = str(exc)
                    status.update(label="The agent stopped with an error", state="error")

    session = st.session_state.get("diagnosis")
    if not session:
        return
    if session["error"]:
        st.error(f"{session['error'][:500]}\n\nIf this is a rate-limit (429) error, wait a minute and try again.")
        return
    if session["events"] and not streamed_now:
        tool_calls = [e for e in session["events"] if e["type"] == "tool_call"]
        with st.expander(f"Investigation steps ({len(tool_calls)} tool calls)", expanded=False):
            for event in session["events"]:
                show_event(event)
    if session["report"]:
        show_report(session["report"])

    if session["pending"] and not session["action"]:
        st.warning(f"**Approval needed:** {session['pending']['question']}")
        approve, reject = st.columns(2)
        decision = None
        if approve.button("Approve rerun", type="primary", use_container_width=True):
            decision = True
        if reject.button("Reject", use_container_width=True):
            decision = False
        if decision is not None:
            agent = get_agent(*settings)
            for event in agent.resume(session["thread_id"], approved=decision, approver="demo user"):
                if event["type"] == "action_result":
                    session["action"] = event
            st.rerun()
    if session["action"]:
        result = session["action"]["result"]
        if result.get("status") == "Queued":
            st.success(f"Rerun queued: `{result['rerun_id']}` (simulated; in production this calls the ADF REST API).")
        else:
            st.info("Rerun rejected. Nothing was changed.")


if __name__ == "__main__":
    main()
