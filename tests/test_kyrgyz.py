"""Tests for routing a Kyrgyz question through Russian.

The model is faked: these check the decisions around the call, not the quality
of a translation. The decisions are what can go silently wrong - calling the
model for an English question, or mistaking a reworded Russian question for a
Kyrgyz one.
"""

from kalys.kyrgyz import (
    NOT_KYRGYZ,
    TO_KYRGYZ_PROMPT,
    TO_RUSSIAN_PROMPT,
    answer_to_kyrgyz,
    has_cyrillic,
    question_to_russian,
)


class FakeBedrock:
    """Records what it was asked and replies with whatever it was given."""

    def __init__(self, reply: str = NOT_KYRGYZ):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    def ask(self, prompt: str, system: str = "") -> str:
        self.calls.append((prompt, system))
        return self.reply


def test_latin_script_is_not_cyrillic():
    assert not has_cyrillic("What rights does a suspect have?")


def test_russian_and_kyrgyz_are_cyrillic():
    assert has_cyrillic("Какие права есть у подозреваемого?")
    assert has_cyrillic("Шектүү адамдын кандай укуктары бар?")


def test_an_english_question_never_calls_the_model():
    # English is the default language, so it must not pay for a translation.
    bedrock = FakeBedrock()
    question = "What rights does a suspect have?"
    assert question_to_russian(bedrock, question) == (question, False)
    assert bedrock.calls == []


def test_a_kyrgyz_question_is_translated():
    bedrock = FakeBedrock(reply="Какое наказание предусмотрено за кражу?")
    question = "Уурулук үчүн кандай жаза каралган?"
    translated, was_kyrgyz = question_to_russian(bedrock, question)
    assert translated == "Какое наказание предусмотрено за кражу?"
    assert was_kyrgyz is True
    assert bedrock.calls[0][0] == question
    assert bedrock.calls[0][1] == TO_RUSSIAN_PROMPT


def test_a_russian_question_is_left_alone():
    bedrock = FakeBedrock(reply=NOT_KYRGYZ)
    question = "Какие права есть у подозреваемого?"
    assert question_to_russian(bedrock, question) == (question, False)


def test_the_marker_is_recognised_with_stray_punctuation_or_case():
    question = "Какие права есть у подозреваемого?"
    for reply in ["SAME", "same", " SAME. ", "Same."]:
        assert question_to_russian(FakeBedrock(reply=reply), question) == (question, False)


def test_an_empty_reply_leaves_the_question_alone():
    # Better to search with the original than with nothing at all.
    question = "Какие права есть у подозреваемого?"
    assert question_to_russian(FakeBedrock(reply="   "), question) == (question, False)


def test_answer_is_translated_with_the_kyrgyz_prompt():
    bedrock = FakeBedrock(reply="Жооп.")
    assert answer_to_kyrgyz(bedrock, "Ответ.") == "Жооп."
    assert bedrock.calls[0] == ("Ответ.", TO_KYRGYZ_PROMPT)


def test_the_kyrgyz_prompt_protects_the_disclaimer():
    # CLAUDE.md requires this line in English, never translated. Step 9 found
    # Kyrgyz was the language that broke that rule.
    assert "Machine translation. Not an official text." in TO_KYRGYZ_PROMPT
    assert "Never translate that line." in TO_KYRGYZ_PROMPT


def test_the_kyrgyz_prompt_protects_numbers_and_citations():
    assert "Do not recalculate" in TO_KYRGYZ_PROMPT
    assert "Article 97" in TO_KYRGYZ_PROMPT
