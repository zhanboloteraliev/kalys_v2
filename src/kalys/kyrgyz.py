"""Serve a Kyrgyz question by routing it through Russian.

Kyrgyz used to have its own search index and it did not work: hit@10 0.53
against 0.94 for English (Step 12 in LEARNING.md). Both halves of the hybrid
search failed. PostgreSQL has no Kyrgyz stemmer, so keyword search matched
whole words only while Kyrgyz builds meaning by adding suffixes - the law says
`Уурдоо` and a person asks about `Уурулук`. And the embedding model placed
Kyrgyz so poorly that a question about theft landed nearest to kidnapping and
torture.

Measuring showed the articles were always findable and the Kyrgyz *query* was
the problem: translating the question first reached 0.93 in Russian and 0.97 in
English. So Kyrgyz is no longer indexed. A Kyrgyz question is translated into
Russian, searched and answered in Russian, and the answer is translated back.

The answer is translated rather than written in Kyrgyz directly because Step 9
found Kyrgyz was the language the model followed instructions in worst: it
invented an age limit that no article contained and translated the disclaimer
it had been told to keep word for word. Translating a finished answer is a
narrower task than writing one under constraints.

The Kyrgyz source text itself is untouched in `articles`. It is a source of
truth, not a search index.
"""

import logging

log = logging.getLogger("kalys.kyrgyz")

# The model states the language on the first line and puts a translation on the
# second. An earlier version asked it to reply "SAME" for a question that was
# already Russian; it never did. Told elsewhere in the same prompt to use
# precise legal wording, it rewrote the Russian instead ("милиция" became
# "полиция"), and any reply that was not the marker counted as a translation -
# so every Russian question was treated as Kyrgyz and answered in Kyrgyz.
VERDICT_KYRGYZ = "KYRGYZ"
VERDICT_RUSSIAN = "RUSSIAN"
VERDICT_OTHER = "OTHER"

# Kyrgyz uses the Russian alphabet plus these three letters, so finding one
# proves the text is Kyrgyz. Not finding one proves nothing: plenty of Kyrgyz
# sentences use none of them, which is why the model is asked as well.
KYRGYZ_ONLY_LETTERS = frozenset("үөңҮӨҢ")

TO_RUSSIAN_PROMPT = f"""You prepare a question for searching a database of Kyrgyz law.

Reply in exactly one of these three forms and nothing else:

  {VERDICT_RUSSIAN}
  {VERDICT_OTHER}
  {VERDICT_KYRGYZ}
  <the question translated into Russian>

Rules:
- Answer {VERDICT_KYRGYZ} only when the question is written in Kyrgyz, and put the
  Russian translation on the line after it.
- Answer {VERDICT_RUSSIAN} when the question is already in Russian. Reply with that
  one word alone. Do not repeat the question, do not rewrite it, do not improve
  its wording, and do not translate it. Kyrgyz and Russian use almost the same
  alphabet, so judge by the words, not by the letters.
- Answer {VERDICT_OTHER} for any other language, English included.

When translating from Kyrgyz:
- Translate it as a question. Do not answer it, do not turn it into a
  statement, and do not add any fact the question does not contain.
- Use the exact term the Criminal Code and the Criminal Procedure Code of the
  Kyrgyz Republic use, not a near synonym. A near synonym usually names a
  different article: two offences, or two measures of restraint, can be one
  word apart. If two Russian words are close, choose the one used as the title
  of an article.
- Some Kyrgyz legal terms resemble ordinary words with another meaning.
  Translate the legal sense the question is asking about.
- Keep every number, article number, age and date exactly as it appears."""

TO_ENGLISH_PROMPT = """You translate a question about Kyrgyz law from Kyrgyz into English.

- Translate it as a question. Do not answer it, do not turn it into a
  statement, and do not add any fact the question does not contain.
- Use the term the law itself uses, not a near synonym: "detention" and
  "remand in custody" are different articles, and so are "theft" and "robbery".
- Some Kyrgyz legal terms resemble ordinary words with another meaning.
  Translate the legal sense the question is asking about.
- Keep every number, article number, age and date exactly as it appears.
- Reply with the English question alone, with nothing before or after it."""

TO_KYRGYZ_PROMPT = """You translate a legal answer from Russian into Kyrgyz.

- Translate the whole answer, keeping its paragraphs, lists and bold text.
- Keep every article number, part number, edition date, age, period and figure
  exactly as written. Do not recalculate or round anything.
- Keep each citation in the same form, for example
  (Criminal Procedure Code, Article 97, edition 2026-07-28). The act name may
  be given in the answer's language - a reader looking the law up will search
  for it in Russian - but the article number, the part and the edition date
  must stay exactly as written.
- The line "Machine translation. Not an official text." must appear in English,
  exactly as given, on its own line at the very end. Never translate that line.
- Reply with the translated answer alone, with no note about the translation."""

# Any Cyrillic letter. A question in Latin script cannot be Kyrgyz, so it needs
# no model call at all - which keeps English, the default language, free.
CYRILLIC_RANGE = range(0x0400, 0x0530)


def has_cyrillic(text: str) -> bool:
    """Whether the text contains any Cyrillic letter."""
    return any(ord(character) in CYRILLIC_RANGE for character in text)


def has_kyrgyz_letters(text: str) -> bool:
    """Whether the text uses a letter that exists in Kyrgyz but not Russian."""
    return any(character in KYRGYZ_ONLY_LETTERS for character in text)


def question_to_russian(bedrock, question: str) -> tuple[str, bool]:
    """Return (question to search with, whether it was Kyrgyz).

    A question in Latin script is returned untouched without calling the model,
    which keeps English - the default language - free.

    Anything with Cyrillic in it is sent to the model, which answers with the
    language on the first line and, for Kyrgyz, the Russian translation on the
    second. Three things have to hold before the Kyrgyz path is taken: the
    verdict says Kyrgyz, there is a translation, and the translation is not
    simply the question handed back. Anything else is treated as not Kyrgyz.

    Failing that way round is deliberate. Treating Russian as Kyrgyz answers a
    Russian speaker in a language they may not read, which is plainly broken;
    treating Kyrgyz as Russian only makes its retrieval worse, which is the
    situation this module was written to improve. A bad guess should cost
    quality, not correctness.
    """
    if not has_cyrillic(question):
        return question, False

    reply = bedrock.ask(question, system=TO_RUSSIAN_PROMPT).strip()
    first_line, _, rest = reply.partition("\n")
    verdict = first_line.strip().strip(".:").upper()

    if verdict == VERDICT_KYRGYZ:
        translation = rest.strip()
    elif verdict in (VERDICT_RUSSIAN, VERDICT_OTHER):
        # Believe "not Kyrgyz" unless the letters say otherwise.
        translation = rest.strip() if has_kyrgyz_letters(question) else ""
    else:
        # The model ignored the format, which is what the previous prompt
        # provoked. Treat the whole reply as a translation only when the
        # question's own letters prove it is Kyrgyz.
        translation = reply if has_kyrgyz_letters(question) else ""

    usable = (
        bool(translation)
        # A reply that is the question handed back is not a translation.
        and translation.casefold() != question.strip().casefold()
        # Nor is one that still contains letters Russian does not have.
        and not has_kyrgyz_letters(translation)
    )
    if usable:
        return translation, True
    if has_kyrgyz_letters(question):
        # Searching the Kyrgyz original is the weak path this module exists to
        # avoid, so record it rather than degrade silently.
        log.warning("Kyrgyz question but no usable translation; reply was %r", reply[:120])
    return question, False


def question_to_english(bedrock, question: str) -> str:
    """Translate a Kyrgyz question into English, or return '' if unusable.

    Only called for a question already judged to be Kyrgyz, so there is no
    verdict line to parse here - one job, one reply. An English translation is
    written in Latin script, so any Cyrillic left in the reply means the model
    did something other than translate, and the caller falls back to Russian.
    """
    reply = bedrock.ask(question, system=TO_ENGLISH_PROMPT).strip()
    if not reply or has_cyrillic(reply):
        log.warning("No usable English translation; reply was %r", reply[:120])
        return ""
    return reply


def route_question(bedrock, question: str) -> tuple[str, str, bool]:
    """Return (question to search with, question to answer from, was Kyrgyz).

    The two are the same question in every language but Kyrgyz. For Kyrgyz they
    differ on purpose, because the best language to *search* in is not the best
    language to *answer* from:

    - Search in English. Measured on the 30 Kyrgyz eval questions, searching
      with an English translation scores hit@10 1.00 and MRR 0.873, against
      0.97 / 0.828 for Russian (Step 20e). The index holds an English
      translation of every article, so an English query compares like with
      like, while a Russian query is matched against English text by the
      embedding model alone.
    - Answer from Russian. The answer is written in Russian and then translated
      into Kyrgyz, because Russian and Kyrgyz share legal vocabulary and Step 9
      found Kyrgyz was the language the model followed instructions in worst.

    Two small calls rather than one reply carrying both translations: a
    multi-line protocol is what broke in Step 19, and each of these can fail on
    its own without taking the other down. If the English one fails, the search
    falls back to Russian, which is what shipped before Step 20f.
    """
    russian, was_kyrgyz = question_to_russian(bedrock, question)
    if not was_kyrgyz:
        return russian, russian, False
    english = question_to_english(bedrock, question)
    return english or russian, russian, True


def answer_to_kyrgyz(bedrock, answer: str) -> str:
    """Translate a finished Russian answer into Kyrgyz."""
    return bedrock.ask(answer, system=TO_KYRGYZ_PROMPT).strip()
