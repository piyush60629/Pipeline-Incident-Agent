"""Chat model factory for Groq (open-weight models) and Gemini."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel

from .config import default_agent_model, env

REASONING_TOKEN_HEADROOM = 800  # reasoning models spend some output tokens thinking first


def make_chat_model(
    provider: str,
    model: str | None = None,
    *,
    api_key: str | None = None,
    max_tokens: int = 1500,
    temperature: float = 0.0,
    max_retries: int = 6,
    timeout: float = 90,
) -> BaseChatModel:
    """Create a LangChain chat model.

    max_retries is left to the provider SDK here: the Groq client honours the server's
    retry-after header on 429 responses, which is the gentlest way to stay inside free-tier
    per-minute limits during a multi-step agent run.
    """

    model = model or default_agent_model(provider)
    if provider == "groq":
        from langchain_groq import ChatGroq

        key = api_key or env("GROQ_API_KEY")
        if not key:
            raise ValueError("GROQ_API_KEY is not set")
        options: dict[str, Any] = {}
        if model.startswith("openai/gpt-oss"):
            options["reasoning_effort"] = "low"  # reasoning is returned separately; keep it short
            max_tokens += REASONING_TOKEN_HEADROOM
        elif model.startswith("qwen/"):
            options["reasoning_format"] = "hidden"  # final answer only, no <think> text
            max_tokens += REASONING_TOKEN_HEADROOM
        return ChatGroq(
            model=model,
            api_key=key,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=timeout,
            **options,
        )

    if provider == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI

        key = api_key or env("GEMINI_API_KEY")
        if not key:
            raise ValueError("GEMINI_API_KEY is not set")
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=key,
            temperature=temperature,
            max_output_tokens=max_tokens,
            max_retries=max_retries,
            timeout=timeout,
        )

    raise ValueError(f"Unknown LLM provider: {provider}")
