"""Real-provider smoke tests. They need a valid HF_TOKEN and network access to Hugging Face, so they are
skipped by default. They are the ONLY tests that say anything about the real model integration.

    HF_TOKEN=hf_... pytest -m needs_hf
"""

import pytest

from app.config import Settings
from app.llm import HFChatClient

pytestmark = pytest.mark.needs_hf


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def llm(settings) -> HFChatClient:
    return HFChatClient(settings.hf_token, settings.hf_model, settings.hf_base_url, settings.llm_timeout_seconds)


async def test_plain_inference(llm):
    from scripts.verify_hf import check_plain_completion

    assert (await check_plain_completion(llm))[0] == "PASS"


async def test_tool_call_and_mcp_result_roundtrip(llm):
    from scripts.verify_hf import check_tool_call_and_result

    step3, step5 = await check_tool_call_and_result(llm)
    assert step3[0] == "PASS", step3
    assert step5[0] == "PASS", step5
