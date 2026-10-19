"""A scripted chat model for offline tests: returns pre-written messages in order."""

from __future__ import annotations

import itertools
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_ids = itertools.count(1)


def call(tool_name: str, /, **args: Any) -> dict:
    return {"name": tool_name, "args": args, "id": f"call_{next(_ids)}", "type": "tool_call"}


def tool_round(*calls: dict, content: str = "") -> AIMessage:
    return AIMessage(content=content, tool_calls=list(calls))


class ScriptedChatModel(BaseChatModel):
    responses: list[Any]
    seen: list[list] = []
    tool_choices: list[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):  # noqa: D401 - same object, records choice
        self.tool_choices.append(tool_choice)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        if not self.responses:
            raise AssertionError("ScriptedChatModel ran out of responses")
        message = self.responses.pop(0)
        if isinstance(message, Exception):
            raise message
        return ChatResult(generations=[ChatGeneration(message=message)])


def final_text(text: str = "Root cause found; see summary.") -> AIMessage:
    """The agent's closing message: plain text, no tool calls."""

    return AIMessage(content=text)


def report_json(**fields: Any) -> AIMessage:
    """The report step's reply."""

    import json

    return AIMessage(content=json.dumps(fields))
