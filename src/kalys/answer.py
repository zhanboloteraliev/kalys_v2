"""Step 7: answer a question with a Bedrock Claude model, grounded in the
articles kalys.search finds, with exact citations.

The model is told to answer only from the articles it is given - never from
its own knowledge of the law - and to cite each statement as
"(<act>, Article <number>, edition <date>)". The article text it is given is
always the English translation (every current-edition article has one,
except a handful of kg-only articles - see LEARNING.md, Step 4), so the
model is also told to translate it itself when answering in Russian or
Kyrgyz, and to add the required "Machine translation. Not an official
text." label.

Needs AWS credentials (see LEARNING.md, Step 7, for how we set this up) and
access to a Claude model in Bedrock - set AWS_PROFILE in .env if you used a
named profile.

Run:
  docker compose up -d db
  uv run python -m kalys.embed             (once, after kalys.translate)
  uv run python -m kalys.answer "how is a child questioned as a suspect?"
"""

import json
import os
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import boto3
import psycopg
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from kalys.embed import check_embedder_matches, embedder_from_env
from kalys.search import hybrid_search

load_dotenv()

BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
)
BEDROCK_MAX_RETRIES = 5  # waits 4, 8, 16, 32, 64 seconds on a throttling error
BEDROCK_MAX_ANSWER_TOKENS = 4096  # a real answer with several citations needs more than 1024
SCHEMA_FILE = Path("db/schema.sql")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")
TOP_N = 5

SYSTEM_PROMPT = """You are Kalys, a legal-search assistant for the law of the
Kyrgyz Republic. You have the Constitution, the Criminal Code, and the Criminal
Procedure Code. Under each question you are given the articles a search found
for it.

How to answer:
- Write for someone who is not a lawyer: plain words, short paragraphs, the
  practical point first. A real conversation, not a legal brief.
- Most questions come from a real situation ("the police stopped me"), not from
  a legal term. Work out which of the given articles bear on that situation and
  answer from them.
- Begin with what the articles DO tell this person. Never open with what is
  missing, and never list the articles you did not use.
- Everything you say about the law must come from the articles below, never from
  your own knowledge of Kyrgyz law. This is the one rule that has no exception.
- Legal rules nearly always carry a condition, and an article often gives
  different figures for different situations. Keep each condition attached to
  the rule it governs, and never present an exception as the general rule. Where
  an article gives two figures, say which situation each one belongs to instead
  of choosing one. Never merge two provisions into a single rule, and never
  attach a figure to a rule it does not belong to. Being plain must not cost the
  reader the condition.
- Be especially careful with numbers, deadlines and ages. Before you state one,
  find the exact words for it in the article and keep its condition. Do not
  assume a figure that is common in other countries (a 48-hour limit, an age of
  16) - the only figure that counts is the one the given article states for the
  situation asked about. If the article names no age, do not supply one.
- Give a time limit the way the article gives it - a length of time measured
  from a starting point, such as "45 hours from the moment of actual detention"
  - and do not work out the clock time or calendar date that it lands on.
  Arithmetic across hours and days is easy to get wrong, and a wrong deadline is
  more dangerous than no deadline, because it looks like an answer. The reader
  can apply the limit to their own facts. The same goes for working out an age
  from a date of birth.
- Cite every point you take from an article, like this: (<act name>, Article
  <number>, edition <date>). One citation per point is enough - you do not need
  one after every sentence.
- If the articles answer only part of the question, answer that part, then say
  in one short sentence what you could not find. If they answer none of it, say
  briefly what the articles you did get are about, and suggest a way to ask that
  would search better. Use your own words; there is no fixed phrase for this.
- Close with one short follow-up question: the thing you would need to know to
  be more precise, or the obvious next point to look up.
- Questions that are not about law (what a word means, how to put a question,
  what you can do) you may answer normally, with no citations.

Language and labelling:
- Answer in the language of the question (English, Russian, or Kyrgyz). The
  articles are always in English, machine-translated from the Russian original,
  so translate the parts you use yourself.
- Whenever your answer states what the law says, put this line at the very
  end, after your follow-up question, alone on its own line:
  "Machine translation. Not an official text."
  Write it in English exactly as given, even when the rest of your answer is in
  Russian or Kyrgyz. Never translate it and never add formatting to it.
- A greeting or small talk needs no citations and no label. Reply warmly and
  briefly, and say what you can look up."""


RETRYABLE_ERROR_CODES = {
    # Bedrock throttles (and occasionally has a transient service error)
    # under load; both are worth retrying, unlike a 4xx like "bad request"
    # or "model not found".
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
    "InternalServerException",
}


@dataclass(frozen=True)
class ArticleContext:
    citation: str
    title: str
    text: str


class BedrockClient:
    """Wraps the Bedrock Runtime API for a Claude model: `ask` for one full
    answer, `ask_stream` for the same answer delivered in pieces as the
    model generates them (used by the chat web UI's "typing" effect). Tests
    use a fake with the same methods, or a fake boto3 client via `client=`."""

    def __init__(self, client=None, model: str = BEDROCK_MODEL_ID, sleep=time.sleep):
        self.client = client or boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        self.model = model
        self.sleep = sleep  # a parameter so tests can run without real waiting

    def _request_body(self, prompt: str) -> str:
        return json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": BEDROCK_MAX_ANSWER_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }
        )

    def ask(self, prompt: str) -> str:
        body = self._request_body(prompt)
        for attempt in range(BEDROCK_MAX_RETRIES + 1):
            try:
                response = self.client.invoke_model(modelId=self.model, body=body)
                payload = json.loads(response["body"].read())
                return payload["content"][0]["text"]
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in RETRYABLE_ERROR_CODES or attempt == BEDROCK_MAX_RETRIES:
                    raise
                self.sleep(2 ** (attempt + 2))
        raise RuntimeError("unreachable")

    def ask_stream(self, prompt: str) -> Iterator[str]:
        """Like `ask`, but yields the answer in small pieces as the model
        generates them. Only the initial request is retried on a throttling
        error; once pieces have started arriving, an error is left to
        propagate rather than silently restarting a partially shown answer."""
        body = self._request_body(prompt)
        response = None
        for attempt in range(BEDROCK_MAX_RETRIES + 1):
            try:
                response = self.client.invoke_model_with_response_stream(
                    modelId=self.model, body=body
                )
                break
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in RETRYABLE_ERROR_CODES or attempt == BEDROCK_MAX_RETRIES:
                    raise
                self.sleep(2 ** (attempt + 2))
        if response is None:
            raise RuntimeError("unreachable")

        for event in response["body"]:
            chunk = json.loads(event["chunk"]["bytes"])
            if chunk.get("type") == "content_block_delta":
                delta = chunk["delta"].get("text", "")
                if delta:
                    yield delta


def build_prompt(question: str, contexts: list[ArticleContext]) -> str:
    sources = "\n\n".join(f"[{c.citation}] {c.title}\n{c.text}" for c in contexts)
    return f"Question: {question}\n\nArticles:\n{sources}"


def fetch_context(conn, edition_id: int, number: str) -> ArticleContext | None:
    """The English translation and citation for one search result. Returns
    None for the handful of kg-only articles with no translation (see
    LEARNING.md, Step 4) - we skip those rather than show the model a gap."""
    row = conn.execute(
        """
        SELECT a.name_en, e.edition_date, t.title, t.text
        FROM translations t
        JOIN editions e ON e.id = t.edition_id
        JOIN acts a ON a.slug = e.act_slug
        WHERE t.edition_id = %s AND t.number = %s
        """,
        (edition_id, number),
    ).fetchone()
    if row is None:
        return None
    name_en, edition_date, title, text = row
    citation = f"{name_en}, Article {number} ({edition_date})"
    return ArticleContext(citation=citation, title=title, text=text)


def gather_contexts(conn, embedder, question: str, limit: int = TOP_N) -> list[ArticleContext]:
    hits = hybrid_search(conn, embedder, question, limit=limit)
    return [c for c in (fetch_context(conn, e, n) for e, n in hits) if c is not None]


def answer(conn, embedder, bedrock, question: str, limit: int = TOP_N) -> str:
    contexts = gather_contexts(conn, embedder, question, limit)
    if not contexts:
        return "No matching articles were found for this question."
    return bedrock.ask(build_prompt(question, contexts))


def main() -> None:
    question = " ".join(sys.argv[1:]) or input("Question: ")
    embedder = embedder_from_env()
    bedrock = BedrockClient()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        check_embedder_matches(conn, embedder)
        print(answer(conn, embedder, bedrock, question))


if __name__ == "__main__":
    main()
