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

# The model answers with exactly this when the question is not Kyrgyz, which
# is cheaper and far more reliable than comparing the reply to the input: a
# model asked to "return it unchanged" often rewords a little, and any
# difference would then look like a translation.
NOT_KYRGYZ = "SAME"

TO_RUSSIAN_PROMPT = f"""You prepare a question for searching a database of Kyrgyz law.

- If the question is written in Kyrgyz, translate it into Russian.
- If it is written in any other language, reply with exactly {NOT_KYRGYZ} and nothing else.
- Reply with the translated question alone: no quotes, no label, no explanation.
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


def question_to_russian(bedrock, question: str) -> tuple[str, bool]:
    """Return (question to search with, whether it was Kyrgyz).

    A question in Latin script is returned untouched without calling the
    model. Anything with Cyrillic in it is sent to the model, which replies
    either with a Russian translation or with the marker meaning "not Kyrgyz".
    """
    if not has_cyrillic(question):
        return question, False

    reply = bedrock.ask(question, system=TO_RUSSIAN_PROMPT).strip()
    if reply.strip(".").upper() == NOT_KYRGYZ or not reply:
        return question, False
    return reply, True


def answer_to_kyrgyz(bedrock, answer: str) -> str:
    """Translate a finished Russian answer into Kyrgyz."""
    return bedrock.ask(answer, system=TO_KYRGYZ_PROMPT).strip()
