import io
import json

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from kalys.embed import (
    EMBEDDING_DIMENSIONS,
    BedrockEmbedder,
    article_chunks,
    check_embedder_matches,
    embedder_from_env,
    vector_literal,
)


def test_vector_literal_formats_as_pgvector_expects():
    assert vector_literal([0.1, -0.2, 1.0]) == "[0.1,-0.2,1.0]"


def test_vector_literal_handles_empty_list():
    assert vector_literal([]) == "[]"


def test_article_chunks_keeps_the_whole_article_and_each_part():
    # '' is the whole article. A general question matches that; a specific one
    # matches a single part. Both are needed, so both are embedded.
    rows = [(1, "97", "Detention", "1. First rule.\n2. Second rule.")]
    chunks = article_chunks(rows)
    assert [(edition, number, part) for edition, number, part, _ in chunks] == [
        (1, "97", ""),
        (1, "97", "1"),
        (1, "97", "2"),
    ]


def test_article_chunks_carries_the_title_into_every_chunk():
    rows = [(1, "97", "Detention", "1. First rule.\n2. Second rule.")]
    for *_, text in article_chunks(rows):
        assert text.startswith("Detention")


def test_the_whole_article_chunk_holds_the_full_text():
    rows = [(1, "97", "Detention", "1. First rule.\n2. Second rule.")]
    whole = next(text for _, _, part, text in article_chunks(rows) if part == "")
    assert "First rule." in whole and "Second rule." in whole


def test_article_without_parts_is_not_embedded_twice():
    # With one part, the part is the article, so there is nothing to add.
    rows = [(1, "36", "Association", "Everyone has the right to freedom of association.")]
    chunks = article_chunks(rows)
    assert len(chunks) == 1
    assert chunks[0][2] == ""
    assert chunks[0][3] == "Association\n\nEveryone has the right to freedom of association."


def test_article_chunks_keeps_articles_separate():
    rows = [
        (1, "97", "Detention", "1. One.\n2. Two."),
        (1, "98", "Search", "1. One."),
    ]
    numbers = [number for _, number, _, _ in article_chunks(rows)]
    assert numbers == ["97", "97", "97", "98"]


# --- the Bedrock embedder, the factory, and the model-mismatch guard ---


class FakeBedrockClient:
    """Stands in for boto3's bedrock-runtime client."""

    def __init__(self, errors_before_success=0, error_code="ThrottlingException", error=None):
        self.errors_before_success = errors_before_success
        self.error_code = error_code
        self.error = error  # an exception instance, instead of a ClientError code
        self.calls = []

    def invoke_model(self, modelId, body):  # noqa: N803 - boto3 names it this way
        self.calls.append(json.loads(body))
        if self.errors_before_success > 0:
            self.errors_before_success -= 1
            if self.error is not None:
                raise self.error
            raise ClientError({"Error": {"Code": self.error_code}}, "InvokeModel")
        return {"body": io.BytesIO(json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode())}


def _embedder(client, waits=None):
    # `waits` collects the sleep lengths so a test can check the backoff
    # without actually waiting. Note it must not be written as `waits or []`:
    # an empty list is falsy, so that would quietly append to a throwaway one.
    recorded = [] if waits is None else waits
    return BedrockEmbedder(client=client, sleep=recorded.append)


def test_bedrock_embedder_returns_one_vector_per_text():
    client = FakeBedrockClient()
    assert _embedder(client).encode(["a", "b", "c"]) == [[0.1, 0.2, 0.3]] * 3
    assert len(client.calls) == 3


def test_bedrock_embedder_asks_for_the_dimensions_the_schema_expects():
    client = FakeBedrockClient()
    _embedder(client).encode(["a"])
    assert client.calls[0]["dimensions"] == EMBEDDING_DIMENSIONS
    assert client.calls[0]["normalize"] is True
    assert client.calls[0]["inputText"] == "a"


def test_bedrock_embedder_retries_a_throttling_error():
    client = FakeBedrockClient(errors_before_success=2)
    waits = []
    assert _embedder(client, waits).encode(["a"]) == [[0.1, 0.2, 0.3]]
    assert waits == [4, 8]  # backoff doubles


def test_bedrock_embedder_retries_a_connection_error():
    # A real run died here: one dropped connection out of ~11,000 requests
    # ended the whole embedding run, and nothing is saved until a language is
    # finished, so 20 minutes of work went with it.
    error = EndpointConnectionError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/")
    client = FakeBedrockClient(errors_before_success=2, error=error)
    waits = []
    assert _embedder(client, waits).encode(["a"]) == [[0.1, 0.2, 0.3]]
    assert waits == [4, 8]


def test_bedrock_embedder_gives_up_on_a_connection_error_that_keeps_happening():
    error = EndpointConnectionError(endpoint_url="https://bedrock-runtime.us-east-1.amazonaws.com/")
    client = FakeBedrockClient(errors_before_success=99, error=error)
    with pytest.raises(EndpointConnectionError):
        _embedder(client).encode(["a"])


def test_bedrock_embedder_does_not_retry_a_bad_request():
    client = FakeBedrockClient(errors_before_success=1, error_code="ValidationException")
    with pytest.raises(ClientError):
        _embedder(client).encode(["a"])


def test_embedder_from_env_rejects_an_unknown_name(monkeypatch):
    monkeypatch.setenv("KALYS_EMBEDDER", "openai")
    with pytest.raises(ValueError, match="must be 'bedrock' or 'local'"):
        embedder_from_env()


class FakeConn:
    def __init__(self, models):
        self.models = models

    def execute(self, sql, params=None):
        return self

    def fetchall(self):
        return [(model,) for model in self.models]


class FakeEmbedder:
    model_name = "amazon.titan-embed-text-v2:0"


def test_check_embedder_matches_accepts_the_same_model():
    check_embedder_matches(FakeConn(["amazon.titan-embed-text-v2:0"]), FakeEmbedder())


def test_check_embedder_matches_accepts_an_empty_table():
    # Nothing is embedded yet, which is the normal state before the first run.
    check_embedder_matches(FakeConn([]), FakeEmbedder())


def test_check_embedder_matches_rejects_vectors_from_another_model():
    # Comparing vectors from two models returns plausible nonsense, so this
    # has to be noisy rather than silent.
    with pytest.raises(RuntimeError, match="were made by"):
        check_embedder_matches(FakeConn(["paraphrase-multilingual-mpnet-base-v2"]), FakeEmbedder())
