import pytest

from app.agent import is_in_scope


@pytest.mark.parametrize(
    "text",
    [
        "I need help with a treadmill.",
        "What repair services are available?",
        "Track my demo request",
        "I have a safety concern",
        "hello",
        "how much does a quote cost",
        "my elliptical makes a loud noise",
    ],
)
def test_in_scope(text):
    assert is_in_scope(text)


@pytest.mark.parametrize(
    "text",
    [
        "what is python",
        "write me a poem about the sea",
        "who won the football match yesterday",
        "solve 2x + 3 = 11",
    ],
)
def test_out_of_scope(text):
    assert not is_in_scope(text)


async def test_off_topic_message_never_reaches_the_model():
    from app.agent import OUT_OF_SCOPE_REPLY, run_agent
    from fakes import FakeTools

    class ExplodingLLM:
        async def complete(self, *args, **kwargs):
            raise AssertionError("the model must not be called for off-topic messages")

    result = await run_agent(ExplodingLLM(), FakeTools(), [], "what is python")
    assert result.text == OUT_OF_SCOPE_REPLY
    assert result.tools_used == []
