"""Test doubles. A scripted model proves the app's logic; it does NOT prove the real provider works."""

from typing import Any

from app.llm import LLMMessage, ToolCall


class ScriptedLLM:
    """Returns pre-written messages in order, and records what it was sent."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages, tools=None, tool_choice=None):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools, "tool_choice": tool_choice})
        if not self.script:
            raise AssertionError("unexpected LLM call (the model must not be used here)")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def tool_call_message(*calls: tuple[str, str, str]) -> LLMMessage:
    """tool_call_message(("call_1", "search_services", '{"query": "x"}'))"""
    return LLMMessage(content=None, tool_calls=[ToolCall(id=i, name=n, arguments=a) for i, n, a in calls])


def text_message(text: str) -> LLMMessage:
    return LLMMessage(content=text)


class FakeTools:
    """A stand-in for the MCP client in agent-loop unit tests (no database needed)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def openai_tool_specs(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_services",
                    "description": "search",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                },
            }
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((name, arguments or {}))
        return {"ok": True, "matches": [{"title": "Upholstery repair", "source_url": "https://sportfitkw.com/service.html"}]}
