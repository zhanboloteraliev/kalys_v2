"""Step 6: compute a semantic embedding for every current-edition article,
in each language it has text in: English (the translation), Russian, and
Kyrgyz (the originals). Used by kalys.search for semantic (meaning-based)
search, alongside keyword search.

One embedding per (edition, article number, language), so a question is
compared against embeddings of the *same* language, not against English
embeddings via a cross-lingual guess. Early testing found the multilingual
model used here is noticeably weaker at cross-lingual matching for Kyrgyz
than for Russian or English - see the Step 6 entry in LEARNING.md - so
same-language comparison matters more for kg than it might for ru.

A local model: no API key, no network call after it is downloaded once.

Run:
  docker compose up -d db
  uv run python -m kalys.translate   (translations must already exist)
  uv run python -m kalys.embed
"""

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
from dotenv import load_dotenv

from kalys.chunk import chunk_text, split_into_parts

# Without this, a local run picks up the account's stale default AWS profile
# instead of AWS_PROFILE=kalys from .env, and Bedrock answers
# UnrecognizedClientException. On the server .env has no AWS_PROFILE on
# purpose (the instance role provides the credentials), and load_dotenv never
# overwrites a real environment variable, so this is safe there.
load_dotenv()

LOCAL_EMBEDDING_MODEL = "paraphrase-multilingual-mpnet-base-v2"
BEDROCK_EMBEDDING_MODEL = "amazon.titan-embed-text-v2:0"
EMBEDDING_DIMENSIONS = 1024  # must match the vector(...) width in db/schema.sql
BEDROCK_REGION = os.environ.get("AWS_REGION", "us-east-1")
BEDROCK_MAX_RETRIES = 5  # waits 4, 8, 16, 32, 64 seconds on a throttling error
RETRYABLE_ERROR_CODES = (
    "ThrottlingException",
    "ServiceUnavailableException",
    "ModelTimeoutException",
)
# Titan embeds one text per request, so the only way to go faster is to send
# several requests at once. Eight is well inside the default account limits.
BEDROCK_EMBED_WORKERS = 8

SCHEMA_FILE = Path("db/schema.sql")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")


class LocalEmbedder:
    """Wraps a local sentence-embedding model. Tests use a fake with the same
    `encode` method, so they do not need to download a model."""

    def __init__(self, model_name: str = LOCAL_EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer  # slow import, so it is lazy

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


class BedrockEmbedder:
    """Amazon Titan Text Embeddings V2, through Bedrock.

    Replaces the local model, which handled Russian poorly and Kyrgyz badly
    (see the Step 12 entry in LEARNING.md). Titan was chosen over Cohere only
    because Cohere models on Bedrock need an AWS Marketplace subscription that
    this account does not have; Titan is Amazon's own and needs none.

    Titan is symmetric: the same call embeds a document and a question. Cohere
    is not - it needs `input_type` set differently for each, and getting that
    wrong quietly costs accuracy - so there is one less way to be wrong here.

    It accepts one text per request, so `encode` sends several requests at
    once. Tests use a fake with the same `encode` method.
    """

    def __init__(self, model_name: str = BEDROCK_EMBEDDING_MODEL, client=None, sleep=time.sleep):
        import boto3  # slow import, so it is lazy

        self.model_name = model_name
        self.client = client or boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)
        self.sleep = sleep  # a parameter so tests can run without real waiting

    def _encode_one(self, text: str) -> list[float]:
        from botocore.exceptions import ClientError

        body = json.dumps(
            {"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True}
        )
        for attempt in range(BEDROCK_MAX_RETRIES + 1):
            try:
                response = self.client.invoke_model(modelId=self.model_name, body=body)
                return json.loads(response["body"].read())["embedding"]
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code")
                if code not in RETRYABLE_ERROR_CODES or attempt == BEDROCK_MAX_RETRIES:
                    raise
                self.sleep(2 ** (attempt + 2))
        raise RuntimeError("unreachable")

    def encode(self, texts: list[str]) -> list[list[float]]:
        if len(texts) == 1:  # the common case when answering a question
            return [self._encode_one(texts[0])]
        with ThreadPoolExecutor(max_workers=BEDROCK_EMBED_WORKERS) as pool:
            return list(pool.map(self._encode_one, texts))


def embedder_from_env():
    """The embedder named by KALYS_EMBEDDER: 'bedrock' (default) or 'local'.

    The vectors stored in the database and the vector made from a question
    must come from the same model, or the comparison between them is
    meaningless. `check_embedder_matches` enforces that at startup.
    """
    choice = os.environ.get("KALYS_EMBEDDER", "bedrock").lower()
    if choice == "local":
        return LocalEmbedder()
    if choice == "bedrock":
        return BedrockEmbedder()
    raise ValueError(f"KALYS_EMBEDDER must be 'bedrock' or 'local', not {choice!r}")


def check_embedder_matches(conn, embedder) -> None:
    """Fail loudly if the stored vectors came from a different model.

    Comparing a question embedded by one model against articles embedded by
    another returns results that look plausible and are nonsense. That is a
    silent failure, and the whole point of this check is to make it noisy.
    """
    rows = conn.execute("SELECT DISTINCT model FROM embeddings").fetchall()
    stored = {model for (model,) in rows}
    if stored and stored != {embedder.model_name}:
        raise RuntimeError(
            f"embeddings in the database were made by {sorted(stored)}, but questions "
            f"are being embedded by {embedder.model_name!r}. Re-run "
            f"`python -m kalys.embed`, or set KALYS_EMBEDDER to match."
        )


def article_chunks(rows: list[tuple[int, str, str, str]]) -> list[tuple[int, str, str, str]]:
    """Turn (edition_id, number, title, text) rows into one entry per part.

    Returns (edition_id, number, part, text to embed), with part '' meaning
    the whole article.

    Every article is embedded whole *and* split into its numbered parts,
    because the two kinds of question need different things. "How long can the
    police hold me" wants part 5 of Article 97, which is lost in the average of
    a 2,697-character article. "What does the Constitution say about the
    presumption of innocence" wants the whole of Article 57, and matches no
    single part of it well. Measured both ways: parts alone fixed the first
    kind of question and broke the second (see the Step 13 entry in
    LEARNING.md), so we keep both and let the search pick.
    """
    chunks = []
    for edition_id, number, title, text in rows:
        chunks.append((edition_id, number, "", chunk_text(title, "", text)))
        parts = split_into_parts(text)
        if len(parts) > 1:
            # A single part is the whole article again, already added above.
            for part, body in parts:
                if part:
                    chunks.append((edition_id, number, part, chunk_text(title, part, body)))
    return chunks


def vector_literal(values: list[float]) -> str:
    """A Python list of floats as the text pgvector expects, e.g. "[0.1,0.2]"."""
    return "[" + ",".join(repr(v) for v in values) + "]"


def current_translations(conn) -> list[tuple[int, str, str, str]]:
    """(edition_id, number, title, text) for every translated ('en') article."""
    return conn.execute("SELECT edition_id, number, title, text FROM translations").fetchall()


def current_edition_originals(conn, lang: str) -> list[tuple[int, str, str, str]]:
    """(edition_id, number, title, text) of one original language ('ru' or
    'kg'), for the newest edition of each act. Some articles have no row in
    a given language (see LEARNING.md, Step 4) - we just embed what exists."""
    return conn.execute(
        """
        SELECT ar.edition_id, ar.number, ar.title, ar.text
        FROM articles ar
        JOIN editions e ON ar.edition_id = e.id
        WHERE ar.lang = %s
          AND e.edition_code = (
              SELECT max(edition_code) FROM editions WHERE act_slug = e.act_slug
          )
        """,
        (lang,),
    ).fetchall()


def save_embedding(
    conn, edition_id: int, number: str, part: str, lang: str, vector: list[float], model: str
) -> None:
    conn.execute(
        "INSERT INTO embeddings (edition_id, number, part, lang, embedding, model) "
        "VALUES (%s, %s, %s, %s, %s::vector, %s) "
        "ON CONFLICT (edition_id, number, lang, part) DO UPDATE SET "
        "embedding = EXCLUDED.embedding, model = EXCLUDED.model, created_at = now()",
        (edition_id, number, part, lang, vector_literal(vector), model),
    )


def embed_language(conn, embedder, lang: str, rows: list[tuple[int, str, str, str]]) -> int:
    """Embed every part of every article in one language. Returns the count.

    Old rows for this language are removed first: an article that used to be
    stored whole now becomes several parts, and the row for the whole article
    would otherwise stay behind and compete with them.
    """
    chunks = article_chunks(rows)
    print(f"Embedding {len(chunks)} {lang} parts from {len(rows)} articles ...")
    vectors = embedder.encode([text for _, _, _, text in chunks])
    conn.execute("DELETE FROM embeddings WHERE lang = %s", (lang,))
    for (edition_id, number, part, _), vector in zip(chunks, vectors, strict=True):
        save_embedding(conn, edition_id, number, part, lang, vector, embedder.model_name)
    return len(chunks)


def main() -> None:
    embedder = embedder_from_env()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        # Kyrgyz is no longer a search language (Step 14): a Kyrgyz question
        # is translated into Russian and searched as Russian. The Kyrgyz
        # source text stays in `articles` untouched - it is a source of truth,
        # not a search index.
        by_language = {
            "en": current_translations(conn),
            "ru": current_edition_originals(conn, "ru"),
        }
        saved = 0
        for lang, rows in by_language.items():
            saved += embed_language(conn, embedder, lang, rows)

        print(f"Saved {saved} embeddings.")


if __name__ == "__main__":
    main()
