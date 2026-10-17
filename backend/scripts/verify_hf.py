"""Verify the Hugging Face model integration with REAL credentials.

Run from the backend folder, with your token in the environment (never paste it into chat or commit it):

    PowerShell:   $env:HF_TOKEN = "hf_..."; python -m scripts.verify_hf
    bash:         HF_TOKEN=hf_... python -m scripts.verify_hf

Optional: HF_MODEL (default openai/gpt-oss-120b, optionally with a ":provider" suffix), HF_BASE_URL.
No database is needed: only the search tools are used. Prints PASS / FAIL / SKIP per check and exits
non-zero if a required check fails. The token is never printed.
"""

import asyncio
import json
import os
import sys

import httpx

from app.agent import run_agent
from app.config import Settings
from app.llm import HFChatClient, LLMUnavailableError
from app.mcp_client import McpToolClient

Result = tuple[str, str]  # (PASS|FAIL|SKIP, detail)


async def check_model_listed(settings: Settings) -> Result:
    """1. The model is available through the router."""
    base_model = settings.hf_model.split(":")[0]
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.get(
                settings.hf_base_url.rstrip("/") + "/models", headers={"Authorization": f"Bearer {settings.hf_token}"}
            )
    except httpx.HTTPError as exc:
        return "SKIP", f"could not list models ({type(exc).__name__})"
    if r.status_code != 200:
        return "SKIP", f"model listing returned HTTP {r.status_code}"
    entries = r.json().get("data", [])
    match = next((m for m in entries if m.get("id") == base_model), None)
    if match is None:
        return "FAIL", f"{base_model} is not in the router's model list ({len(entries)} models listed)"
    providers = match.get("providers") or []
    summary = ", ".join(
        f"{p.get('provider')}{' (tools)' if p.get('supports_tools') else ''}" for p in providers if isinstance(p, dict)
    )
    return "PASS", f"{base_model} is listed" + (f"; providers: {summary}" if summary else "")


async def check_plain_completion(llm: HFChatClient) -> Result:
    """2. A successful inference request."""
    try:
        msg = await llm.complete([{"role": "user", "content": "Reply with the single word: ready"}])
    except LLMUnavailableError as exc:
        return "FAIL", f"inference failed: {exc}"
    if not (msg.content or "").strip():
        return "FAIL", "the model returned an empty reply"
    return "PASS", f"reply: {(msg.content or '').strip()[:60]!r}"


async def check_tool_call_and_result(llm: HFChatClient) -> tuple[Result, Result]:
    """3. The model requests a tool; 4+5. a REAL MCP result goes back and a final answer comes out."""
    async with McpToolClient(os.environ.get("DATABASE_URL", "postgresql+psycopg://unused@127.0.0.1:1/unused")) as mcp:
        tools = mcp.openai_tool_specs()
        messages = [
            {"role": "system", "content": "Use the provided tools to answer; do not answer from memory."},
            {"role": "user", "content": "Does SportFit offer upholstery repair? Check with a tool."},
        ]
        try:
            first = await llm.complete(messages, tools=tools, tool_choice="auto")
        except LLMUnavailableError as exc:
            return ("FAIL", f"tool request failed: {exc}"), ("SKIP", "no tool call to answer")
        if not first.tool_calls:
            text = (first.content or "").strip()[:80]
            return ("FAIL", f"the model did not request a tool (said: {text!r}). Try another HF_MODEL or pin a provider with ':provider'."), (
                "SKIP",
                "no tool call to answer",
            )
        call = first.tool_calls[0]
        try:
            args = json.loads(call.arguments or "{}")
        except json.JSONDecodeError:
            return ("FAIL", f"tool arguments were not valid JSON: {call.arguments[:80]!r}"), ("SKIP", "no usable tool call")
        step3 = ("PASS", f"model requested {call.name}({json.dumps(args)[:80]})")

        result = await mcp.call_tool(call.name, args)
        messages.append(
            {
                "role": "assistant",
                "content": first.content or "",
                "tool_calls": [{"id": call.id, "type": "function", "function": {"name": call.name, "arguments": call.arguments}}],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)[:6000]})
        try:
            final = await llm.complete(messages, tools=tools, tool_choice="auto")
        except LLMUnavailableError as exc:
            return step3, ("FAIL", f"second request (with tool result) failed: {exc}")
        text = (final.content or "").strip()
        if not text:
            return step3, ("FAIL", "no final answer after the tool result")
        return step3, ("PASS", f"final answer: {text[:100]!r}")


async def check_agent_end_to_end(llm: HFChatClient, settings: Settings) -> Result:
    """Bonus: the app's real agent loop with the real model."""
    async with McpToolClient(os.environ.get("DATABASE_URL", "postgresql+psycopg://unused@127.0.0.1:1/unused")) as mcp:
        try:
            out = await run_agent(llm, mcp, [], "What does SportFit say about preventive maintenance?", settings.max_tool_iterations)
        except LLMUnavailableError as exc:
            return "FAIL", f"agent loop failed: {exc}"
    if not out.tools_used:
        return "FAIL", "agent answered without using any tool"
    return "PASS", f"tools used: {out.tools_used}; sources: {[s['url'] for s in out.sources]}"


def report(label: str, result: Result) -> bool:
    status, detail = result
    print(f"[{status}] {label}: {detail}")
    return status != "FAIL"


async def main() -> int:
    settings = Settings()
    if not settings.hf_token:
        print("HF_TOKEN is not set. Set it in your environment (do not paste it into chat or commit it).")
        return 2
    print(f"Model: {settings.hf_model}\nEndpoint: {settings.hf_base_url}\n")
    llm = HFChatClient(settings.hf_token, settings.hf_model, settings.hf_base_url, settings.llm_timeout_seconds)

    ok = report("1. model available via router", await check_model_listed(settings))
    ok &= report("2. plain inference request", await check_plain_completion(llm))
    step3, step5 = await check_tool_call_and_result(llm)
    ok &= report("3. tool-call request returns a tool call", step3)
    ok &= report("4-5. real MCP result returned, final answer generated", step5)
    ok &= report("bonus: agent loop end to end", await check_agent_end_to_end(llm, settings))
    print("\nAll required checks passed." if ok else "\nSome checks FAILED - see above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
