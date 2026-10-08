import json

import pytest

from kalys.translate import (
    Article,
    GeminiTranslator,
    build_prompt,
    parse_translation,
    translate_article,
)


def test_build_prompt_includes_number_title_and_text():
    article = Article(edition_id=1, number="5", title="Принцип вины", text="1. Текст.")
    assert build_prompt(article) == "Статья 5. Принцип вины\n\n1. Текст."


def test_build_prompt_with_empty_title():
    article = Article(edition_id=1, number="1", title="", text="1. Текст.")
    assert build_prompt(article) == "Статья 1. \n\n1. Текст."


def test_parse_translation_plain_json():
    result = parse_translation('{"title": "Guilt", "text": "1. Text."}')
    assert result == {"title": "Guilt", "text": "1. Text."}


def test_parse_translation_inside_markdown_fence():
    reply = '```json\n{"title": "Guilt", "text": "1. Text."}\n```'
    assert parse_translation(reply) == {"title": "Guilt", "text": "1. Text."}


def test_parse_translation_missing_title_defaults_to_empty():
    assert parse_translation('{"text": "1. Text."}') == {"title": "", "text": "1. Text."}


def test_parse_translation_rejects_reply_without_json():
    with pytest.raises(ValueError):
        parse_translation("Sorry, I cannot help with that.")


class FakeTranslator:
    """Returns one canned reply per prompt, and counts how often it was asked."""

    def __init__(self, reply: str):
        self.reply = reply
        self.model = "fake-model"
        self.calls = 0

    def ask(self, prompt: str) -> str:
        self.calls += 1
        return self.reply


def test_translate_article_calls_the_model_and_returns_the_translation(tmp_path):
    article = Article(edition_id=7, number="1", title="", text="1. Текст.")
    translator = FakeTranslator('{"title": "", "text": "1. Text."}')

    result = translate_article(translator, article, cache_dir=tmp_path)

    assert result == {"title": "", "text": "1. Text."}
    assert translator.calls == 1


def test_translate_article_uses_the_cache_on_the_second_call(tmp_path):
    article = Article(edition_id=7, number="1", title="", text="1. Текст.")
    translator = FakeTranslator('{"title": "", "text": "1. Text."}')

    translate_article(translator, article, cache_dir=tmp_path)
    translate_article(translator, article, cache_dir=tmp_path)

    assert translator.calls == 1  # the second call read the cache file instead


def test_translate_article_writes_a_readable_cache_file(tmp_path):
    article = Article(edition_id=7, number="71-1", title="", text="1. Текст.")
    translator = FakeTranslator('{"title": "Added", "text": "1. Text."}')

    translate_article(translator, article, cache_dir=tmp_path)

    cache_file = tmp_path / "7_71-1_fake-model.json"
    assert json.loads(cache_file.read_text(encoding="utf-8"))["title"] == "Added"


class FakeApiError(Exception):
    def __init__(self, status_code: int):
        self.status_code = status_code


class FakeGeminiClient:
    """A fake google.genai client: fails with some status code a few times, then succeeds."""

    def __init__(self, failures_before_success: int, status_code: int = 429):
        self.failures_before_success = failures_before_success
        self.status_code = status_code
        self.calls = 0
        self.interactions = self

    def create(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures_before_success:
            raise FakeApiError(self.status_code)
        return type("Interaction", (), {"output_text": "ok"})()


class FakeClock:
    def __init__(self):
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)


def test_gemini_translator_retries_on_rate_limit_then_succeeds():
    client = FakeGeminiClient(failures_before_success=2, status_code=429)
    clock = FakeClock()
    translator = GeminiTranslator(client=client, model="gemini-3.8-flash", sleep=clock.sleep)

    result = translator.ask("translate this")

    assert result == "ok"
    assert client.calls == 3
    assert clock.sleeps == [4, 8]  # 2**(0+2), 2**(1+2)


def test_gemini_translator_retries_on_server_overload_then_succeeds():
    # 503 "the model is overloaded" is just as common on the free tier as 429.
    client = FakeGeminiClient(failures_before_success=1, status_code=503)
    clock = FakeClock()
    translator = GeminiTranslator(client=client, model="gemini-3.8-flash", sleep=clock.sleep)

    result = translator.ask("translate this")

    assert result == "ok"
    assert clock.sleeps == [4]


def test_gemini_translator_gives_up_after_too_many_rate_limit_errors():
    client = FakeGeminiClient(failures_before_success=99, status_code=429)
    translator = GeminiTranslator(client=client, model="gemini-3.8-flash", sleep=FakeClock().sleep)

    with pytest.raises(FakeApiError):
        translator.ask("translate this")


def test_gemini_translator_does_not_retry_other_errors():
    def raise_bad_request(**kwargs):
        raise ValueError("bad request")

    client = FakeGeminiClient(failures_before_success=0)
    client.create = raise_bad_request
    translator = GeminiTranslator(client=client, model="gemini-3.8-flash", sleep=FakeClock().sleep)

    with pytest.raises(ValueError):
        translator.ask("translate this")
