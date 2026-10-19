"""Settings in one place: model names, limits and file locations."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KNOWLEDGE_DIR = ROOT / "knowledge_base"

# LLM providers. Groq serves open-weight models with a generous free tier and supports
# tool calling; Gemini is the fallback. Groq retires models regularly:
# https://console.groq.com/docs/deprecations
LLM_PROVIDERS = ("groq", "gemini")
GROQ_AGENT_MODEL = "openai/gpt-oss-120b"
GROQ_JUDGE_MODEL = "qwen/qwen3.8-27b"  # a different model family grades the agent in evaluation
GEMINI_AGENT_MODEL = "gemini-3.5-flash"
GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"

MAX_TOOL_ROUNDS = 6  # investigation rounds before the agent must submit a diagnosis
MAX_TOOL_OUTPUT_CHARS = 1800  # keep tool results short: every round resends the conversation


def env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def resolve_provider(explicit: str | None = None) -> str:
    """The provider that was asked for; otherwise Groq when its key is set, else Gemini."""

    if explicit:
        provider = explicit.strip().lower()
        if provider not in LLM_PROVIDERS:
            raise ValueError(f"LLM provider must be one of {LLM_PROVIDERS}")
        return provider
    return "groq" if env("GROQ_API_KEY") else "gemini"


def default_agent_model(provider: str) -> str:
    return GROQ_AGENT_MODEL if provider == "groq" else GEMINI_AGENT_MODEL
