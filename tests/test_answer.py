import json

import pytest
from botocore.exceptions import ClientError

from kalys.answer import ArticleContext, BedrockClient, build_prompt


def make_client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "test"}}, "InvokeModel")


def test_build_prompt_includes_question_and_citation():
    context = ArticleContext(
        citation="Criminal Code, Article 46 (2026-07-24)",
        title="Necessary Defense",
        text="1. Causing harm in necessary defense is not a crime.",
    )

    prompt = build_prompt("what is necessary defense?", [context])

    assert "what is necessary defense?" in prompt
    assert "[Criminal Code, Article 46 (2026-07-24)] Necessary Defense" in prompt
    assert "1. Causing harm in necessary defense is not a crime." in prompt


def test_build_prompt_with_no_contexts_still_includes_question():
    assert "what is guilt?" in build_prompt("what is guilt?", [])


class FakeBoto3Response:
    """Mimics the StreamingBody-like object Bedrock's invoke_model returns."""

    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


class FakeBedrockRuntimeClient:
    """A fake boto3 bedrock-runtime client: fails a few times, then succeeds."""

    def __init__(self, failures_before_success: int, error_code: str = "ThrottlingException"):
        self.failures_before_success = failures_before_success
        self.error_code = error_code
        self.calls = 0

    def invoke_model(self, modelId: str, body: str):
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise make_client_error(self.error_code)
        return {"body": FakeBoto3Response({"content": [{"text": "ok"}]})}


class FakeClock:
    def __init__(self):
        self.sleeps: list[int] = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def test_bedrock_client_parses_the_models_reply():
    client = FakeBedrockRuntimeClient(failures_before_success=0)
    bedrock = BedrockClient(client=client, model="fake-model", sleep=FakeClock().sleep)

    assert bedrock.ask("a question") == "ok"


def test_bedrock_client_retries_on_throttling_then_succeeds():
    client = FakeBedrockRuntimeClient(failures_before_success=2, error_code="ThrottlingException")
    clock = FakeClock()
    bedrock = BedrockClient(client=client, model="fake-model", sleep=clock.sleep)

    result = bedrock.ask("a question")

    assert result == "ok"
    assert client.calls == 3
    assert clock.sleeps == [4, 8]  # 2**(0+2), 2**(1+2)


def test_bedrock_client_gives_up_after_too_many_throttling_errors():
    client = FakeBedrockRuntimeClient(failures_before_success=99, error_code="ThrottlingException")
    bedrock = BedrockClient(client=client, model="fake-model", sleep=FakeClock().sleep)

    with pytest.raises(ClientError):
        bedrock.ask("a question")


def test_bedrock_client_does_not_retry_other_errors():
    client = FakeBedrockRuntimeClient(failures_before_success=1, error_code="ValidationException")
    bedrock = BedrockClient(client=client, model="fake-model", sleep=FakeClock().sleep)

    with pytest.raises(ClientError):
        bedrock.ask("a question")
