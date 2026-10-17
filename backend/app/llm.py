"""Hugging Face Inference Providers client (OpenAI-compatible chat completions with tool calling).

The router endpoint is https://router.huggingface.co/v1. The model id is configurable via HF_MODEL;
an optional ":provider" suffix pins a provider. Whether a given model/provider pair supports tools
must be verified with ``python -m scripts.verify_hf`` using a real token.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx
from openai import AsyncOpenAI

log = logging.getLogger("sportfit.llm")


class LLMUnavailableError(RuntimeError):
    """The hosted model could not be reached or returned an unusable response."""


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string from the model - untrusted


@dataclass
class LLMMessage:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatLLM(Protocol):
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> LLMMessage: ...


class HFChatClient:
    def __init__(
        self,
        token: str,
        model: str,
        base_url: str,
        timeout: float = 45.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.model = model
        self._client = AsyncOpenAI(
            api_key=token, base_url=base_url, timeout=timeout, max_retries=1, http_client=http_client
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | None = None,
    ) -> LLMMessage:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 700,
            "temperature": 0.2,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        try:
            response = await self._client.chat.completions.create(**kwargs)
            message = response.choices[0].message
        except Exception as exc:
            # Log the failure type and HTTP status only: never the prompt, the response or the token.
            log.warning("LLM call failed: %s status=%s", type(exc).__name__, getattr(exc, "status_code", None))
            raise LLMUnavailableError(type(exc).__name__) from exc
        calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments or "{}")
            for tc in (message.tool_calls or [])
            if getattr(tc, "function", None) is not None
        ]
        return LLMMessage(content=message.content, tool_calls=calls)
