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
from dataclasses import dataclass
from pathlib import Path

import boto3
import psycopg
from botocore.exceptions import ClientError
from dotenv import load_dotenv

from kalys.embed import LocalEmbedder
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

SYSTEM_PROMPT = (
    "You are a legal-search assistant for the law of the Kyrgyz Republic. Answer "
    "only using the articles given to you below the question - never from your own "
    "knowledge of the law, and never add anything the given articles do not "
    'support. If the given articles do not answer the question, say so plainly ("the '
    'articles found do not cover this") instead of guessing.\n'
    "Answer in the same language as the question (English, Russian, or Kyrgyz). "
    "After every statement, cite the article it comes from in parentheses, in this "
    "exact form: (<act name>, Article <number>, edition <date>).\n"
    "The article text given to you is always in English, machine-translated from "
    "the Russian original. If you answer in Russian or Kyrgyz, translate the "
    "relevant parts yourself; either way, end your answer with this line by itself: "
    '"Machine translation. Not an official text."'
)


@dataclass(frozen=True)
class ArticleContext:
    citation: str
    title: str
    text: str


class BedrockClient:
    """Wraps the Bedrock Runtime InvokeModel API for a Claude model. Tests use
    a fake with the same `ask` method, or a fake boto3 client via `client=`."""

    def __init__(self, client=None, model: str = BEDROCK_MODEL_ID, sleep=time.sleep):
        self.client = client or boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        self.model = model
        self.sleep = sleep  # a parameter so tests can run without real waiting

    def ask(self, prompt: str) -> str:
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": BEDROCK_MAX_ANSWER_TOKENS,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        for attempt in range(BEDROCK_MAX_RETRIES + 1):
            try:
                response = self.client.invoke_model(modelId=self.model, body=body)
                payload = json.loads(response["body"].read())
                return payload["content"][0]["text"]
            except ClientError as error:
                # Bedrock throttles (and occasionally has a transient service
                # error) under load; both are worth retrying, unlike a 4xx
                # like "bad request" or "model not found".
                code = error.response.get("Error", {}).get("Code")
                is_retryable = code in {
                    "ThrottlingException",
                    "ServiceUnavailableException",
                    "ModelTimeoutException",
                    "InternalServerException",
                }
                if not is_retryable or attempt == BEDROCK_MAX_RETRIES:
                    raise
                self.sleep(2 ** (attempt + 2))
        raise RuntimeError("unreachable")


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


def answer(conn, embedder, bedrock, question: str, limit: int = TOP_N) -> str:
    hits = hybrid_search(conn, embedder, question, limit=limit)
    contexts = [c for c in (fetch_context(conn, e, n) for e, n in hits) if c is not None]
    if not contexts:
        return "No matching articles were found for this question."
    return bedrock.ask(build_prompt(question, contexts))


def main() -> None:
    question = " ".join(sys.argv[1:]) or input("Question: ")
    embedder = LocalEmbedder()
    bedrock = BedrockClient()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        print(answer(conn, embedder, bedrock, question))


if __name__ == "__main__":
    main()
