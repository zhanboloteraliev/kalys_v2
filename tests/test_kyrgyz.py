"""Tests for routing a Kyrgyz question through Russian.

The model is faked: these check the decisions around the call, not the quality
of a translation. The decisions are what can go silently wrong - calling the
model for an English question, or mistaking a reworded Russian question for a
Kyrgyz one.
"""

from kalys.kyrgyz import (
    TO_ENGLISH_PROMPT,
    TO_KYRGYZ_PROMPT,
    TO_RUSSIAN_PROMPT,
    VERDICT_KYRGYZ,
    VERDICT_OTHER,
    VERDICT_RUSSIAN,
    answer_to_kyrgyz,
    has_cyrillic,
    has_kyrgyz_letters,
    question_to_english,
    question_to_russian,
    route_question,
)


class FakeBedrock:
    """Records what it was asked and replies with whatever it was given."""

    def __init__(self, reply: str = VERDICT_RUSSIAN):
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
    bedrock = FakeBedrock(reply=f"{VERDICT_KYRGYZ}\nКакое наказание предусмотрено за кражу?")
    question = "Уурулук үчүн кандай жаза каралган?"
    translated, was_kyrgyz = question_to_russian(bedrock, question)
    assert translated == "Какое наказание предусмотрено за кражу?"
    assert was_kyrgyz is True
    assert bedrock.calls[0] == (question, TO_RUSSIAN_PROMPT)


def test_a_russian_question_is_left_alone():
    bedrock = FakeBedrock(reply=VERDICT_RUSSIAN)
    question = "Какие права есть у подозреваемого?"
    assert question_to_russian(bedrock, question) == (question, False)


def test_a_rewritten_russian_question_is_not_mistaken_for_kyrgyz():
    # The live bug: asked to mark a Russian question, the model rewrote it
    # instead ("милиция" -> "полиция"), and every Russian question was then
    # answered in Kyrgyz. A reply with no Kyrgyz verdict is not a translation.
    bedrock = FakeBedrock(reply="полиция вымогает у меня деньги")
    question = "милиция вымогает у меня деньги"
    assert question_to_russian(bedrock, question) == (question, False)


def test_a_question_echoed_back_unchanged_is_not_a_translation():
    question = "Что такое залог как мера пресечения?"
    bedrock = FakeBedrock(reply=f"{VERDICT_KYRGYZ}\n{question}")
    assert question_to_russian(bedrock, question) == (question, False)


def test_other_languages_are_left_alone():
    bedrock = FakeBedrock(reply=VERDICT_OTHER)
    question = "Какие права?"  # Cyrillic, so the model is asked
    assert question_to_russian(bedrock, question) == (question, False)


def test_kyrgyz_letters_override_a_wrong_verdict():
    # These three letters do not exist in Russian, so their presence settles it
    # even if the model says otherwise.
    bedrock = FakeBedrock(reply="Когда применяется заключение под стражу?")
    question = "Камакка алуу качан колдонулат? Үй"
    translated, was_kyrgyz = question_to_russian(bedrock, question)
    assert was_kyrgyz is True
    assert translated == "Когда применяется заключение под стражу?"


def test_kyrgyz_with_no_translation_falls_back_to_the_original():
    bedrock = FakeBedrock(reply=VERDICT_KYRGYZ)
    question = "Үй камагы деген эмне?"
    assert question_to_russian(bedrock, question) == (question, False)


def test_a_verdict_with_stray_punctuation_or_case_still_counts():
    question = "Уурулук үчүн кандай жаза каралган?"
    for verdict in ["KYRGYZ", "kyrgyz", " Kyrgyz: ", "KYRGYZ."]:
        bedrock = FakeBedrock(reply=f"{verdict}\nКакое наказание за кражу?")
        assert question_to_russian(bedrock, question)[1] is True


def test_has_kyrgyz_letters():
    assert has_kyrgyz_letters("Шектүү адам")
    assert has_kyrgyz_letters("Өкүм")
    assert not has_kyrgyz_letters("Какие права есть у подозреваемого?")
    assert not has_kyrgyz_letters("What rights does a suspect have?")


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


# --- Step 20f: search in English, answer from Russian -------------------------


class FakeBedrockByPrompt:
    """Replies according to which system prompt it was given.

    `route_question` makes two different calls, so a fake that answers both the
    same way cannot tell them apart.
    """

    def __init__(self, russian: str = "", english: str = ""):
        self.russian = russian
        self.english = english
        self.systems: list[str] = []

    def ask(self, prompt: str, system: str = "") -> str:
        self.systems.append(system)
        if system == TO_ENGLISH_PROMPT:
            return self.english
        return self.russian


def test_english_translation_is_rejected_when_it_is_not_english():
    # Latin script is what an English translation looks like. Anything with
    # Cyrillic in it means the model did something else.
    bedrock = FakeBedrockByPrompt(english="Какое наказание за кражу?")
    assert question_to_english(bedrock, "Уурулук үчүн кандай жаза каралган?") == ""


def test_english_translation_is_returned_when_it_is_english():
    bedrock = FakeBedrockByPrompt(english="What is the punishment for theft?")
    question = "Уурулук үчүн кандай жаза каралган?"
    assert question_to_english(bedrock, question) == "What is the punishment for theft?"
    assert bedrock.systems == [TO_ENGLISH_PROMPT]


def test_a_kyrgyz_question_is_searched_in_english_and_answered_from_russian():
    bedrock = FakeBedrockByPrompt(
        russian=f"{VERDICT_KYRGYZ}\nКакое наказание предусмотрено за кражу?",
        english="What is the punishment for theft?",
    )

    searched, answered, was_kyrgyz = route_question(bedrock, "Уурулук үчүн кандай жаза каралган?")

    assert searched == "What is the punishment for theft?"
    assert answered == "Какое наказание предусмотрено за кражу?"
    assert was_kyrgyz is True


def test_the_search_falls_back_to_russian_when_english_fails():
    # What shipped before Step 20f. A failed English translation must cost
    # retrieval quality, never correctness.
    bedrock = FakeBedrockByPrompt(
        russian=f"{VERDICT_KYRGYZ}\nКакое наказание предусмотрено за кражу?",
        english="",
    )

    searched, answered, was_kyrgyz = route_question(bedrock, "Уурулук үчүн кандай жаза каралган?")

    assert searched == "Какое наказание предусмотрено за кражу?"
    assert answered == searched
    assert was_kyrgyz is True


def test_an_english_question_is_routed_without_any_call():
    bedrock = FakeBedrockByPrompt()
    question = "What rights does a suspect have?"
    assert route_question(bedrock, question) == (question, question, False)
    assert bedrock.systems == []


def test_a_russian_question_is_never_translated_into_english():
    # The English call is for Kyrgyz only: a Russian question must cost exactly
    # one routing call, as it did before.
    bedrock = FakeBedrockByPrompt(russian=VERDICT_RUSSIAN)
    question = "Какие права есть у подозреваемого?"

    assert route_question(bedrock, question) == (question, question, False)
    assert bedrock.systems == [TO_RUSSIAN_PROMPT]
