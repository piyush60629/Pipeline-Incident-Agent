"""Pipeline Incident Agent: a LangGraph agent that diagnoses failed data pipeline runs."""

from __future__ import annotations

from typing import Any

from .config import default_agent_model, resolve_provider
from .graph import IncidentAgent, build_graph
from .llm import make_chat_model

__all__ = ["IncidentAgent", "build_graph", "create_agent", "make_chat_model"]


def create_agent(provider: str | None = None, model: str | None = None, checkpointer: Any = None,
                 environment: str = "azure") -> IncidentAgent:
    """Build the agent with a real model: Groq (gpt-oss-120b) when GROQ_API_KEY is set, else Gemini."""

    provider = resolve_provider(provider)
    llm = make_chat_model(provider, model or default_agent_model(provider))
    return IncidentAgent(llm, checkpointer=checkpointer, environment=environment)
