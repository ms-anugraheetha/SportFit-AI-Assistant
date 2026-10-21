"""Model client and agent-loop tests.

HFChatClient is exercised through the real OpenAI SDK against an in-process fake HTTP server
(httpx.MockTransport) that speaks the OpenAI chat-completions wire format. This verifies request
shape and response parsing. It does NOT prove that Hugging Face's router or a given model works:
run `python -m scripts.verify_hf` with a real token for that.
"""

import json
import os

import httpx
import pytest
from fakes import FakeTools, ScriptedLLM, text_message, tool_call_message

from app.agent import run_agent
from app.llm import HFChatClient, LLMUnavailableError


def completion(message: dict) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": "test/model",
        "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", **message}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def make_client(handler, token="hf_test_token_value") -> HFChatClient:
    return HFChatClient(
        token=token,
        model="test/model:provider",
        base_url="http://hf.invalid/v1",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def test_request_shape_and_tool_call_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json=completion(
                {
                    "content": None,
                    "tool_calls": [
                        {"id": "call_9", "type": "function", "function": {"name": "search_services", "arguments": '{"query": "repair"}'}}
                    ],
                }
            ),
        )

    tools = FakeTools().openai_tool_specs()
    msg = await make_client(handler).complete([{"role": "user", "content": "hi"}], tools=tools)
    assert seen["url"] == "http://hf.invalid/v1/chat/completions"
    assert seen["auth"] == "Bearer hf_test_token_value"
    assert seen["body"]["model"] == "test/model:provider" and seen["body"]["tool_choice"] == "auto"
    assert seen["body"]["tools"][0]["function"]["name"] == "search_services"
    assert [(c.id, c.name, c.arguments) for c in msg.tool_calls] == [("call_9", "search_services", '{"query": "repair"}')]


async def test_plain_text_reply_has_no_tool_calls():
    client = make_client(lambda r: httpx.Response(200, json=completion({"content": "Hello!"})))
    msg = await client.complete([{"role": "user", "content": "hi"}])
    assert msg.content == "Hello!" and msg.tool_calls == []


@pytest.mark.parametrize("status", [401, 429, 500, 503])
async def test_provider_errors_become_llm_unavailable_without_leaking_token(status):
    client = make_client(lambda r: httpx.Response(status, json={"error": "nope"}))
    with pytest.raises(LLMUnavailableError) as info:
        await client.complete([{"role": "user", "content": "hi"}])
    assert "hf_test_token_value" not in str(info.value)


async def test_network_failure_becomes_llm_unavailable():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(LLMUnavailableError):
        await make_client(handler).complete([{"role": "user", "content": "hi"}])


# ---------------------------------------------------------------- agent loop (no database)


async def test_agent_loop_is_capped_and_forces_a_tool_free_final_answer():
    always_tool = tool_call_message(("c", "search_services", '{"query": "x"}'))
    llm = ScriptedLLM([always_tool, always_tool, always_tool, text_message("Final answer.")])
    tools = FakeTools()
    result = await run_agent(llm, tools, [], "treadmill repair question", max_iterations=3)
    assert result.text == "Final answer."
    assert len(llm.calls) == 4 and len(tools.calls) == 3  # 3 capped iterations + 1 final call
    assert llm.calls[-1]["tools"] is None
    assert result.sources == [{"title": "Upholstery repair", "url": "https://sportfitkw.com/service.html"}]


async def test_agent_drops_arguments_not_in_the_tool_schema():
    llm = ScriptedLLM(
        [tool_call_message(("c", "search_services", '{"query": "x", "sql": "DROP TABLE service_requests"}')), text_message("ok")]
    )
    tools = FakeTools()
    await run_agent(llm, tools, [], "treadmill repair")
    assert tools.calls == [("search_services", {"query": "x"})]


async def test_agent_strips_reasoning_blocks_and_caps_length():
    llm = ScriptedLLM([text_message("<think>secret chain of thought</think>" + "A" * 3000)])
    result = await run_agent(llm, FakeTools(), [], "treadmill repair")
    assert "secret" not in result.text and len(result.text) == 1500


async def test_agent_handles_empty_model_output_honestly():
    result = await run_agent(ScriptedLLM([text_message("")]), FakeTools(), [], "treadmill repair")
    assert "wasn't able" in result.text


async def test_agent_caps_parallel_tool_calls():
    many = tool_call_message(*[(f"c{i}", "search_services", '{"query": "x"}') for i in range(7)])
    llm = ScriptedLLM([many, text_message("done")])
    tools = FakeTools()
    await run_agent(llm, tools, [], "treadmill repair")
    assert len(tools.calls) == 4
    tool_msgs = [m for m in llm.calls[1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 7  # every call id gets a reply, so the transcript stays valid


# ---------------------------------------------------------------- real MCP + real SDK client + fake HTTP provider


@pytest.mark.needs_db
async def test_full_loop_real_mcp_result_is_returned_to_the_model(db):
    from app.mcp_client import McpToolClient

    requests_seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests_seen.append(body)
        if len(requests_seen) == 1:
            return httpx.Response(
                200,
                json=completion(
                    {
                        "content": None,
                        "tool_calls": [
                            {"id": "call_a", "type": "function", "function": {"name": "search_services", "arguments": '{"query": "upholstery"}'}}
                        ],
                    }
                ),
            )
        return httpx.Response(200, json=completion({"content": "They list upholstery repair for worn strength equipment."}))

    async with McpToolClient(os.environ["DATABASE_URL"]) as mcp:
        result = await run_agent(make_client(handler), mcp, [], "Do you do upholstery?")

    assert result.tools_used == ["search_services"] and "upholstery" in result.text.lower()
    second = requests_seen[1]["messages"]
    tool_msg = next(m for m in second if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_a" and "svc-upholstery-repair" in tool_msg["content"]
    assert second[-2]["role"] == "assistant" and second[-2]["tool_calls"][0]["id"] == "call_a"
