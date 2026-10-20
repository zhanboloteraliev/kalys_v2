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

import os
from pathlib import Path

import psycopg

from kalys.chunk import chunk_text, split_into_parts

EMBEDDING_MODEL = "paraphrase-multilingual-mpnet-base-v2"
SCHEMA_FILE = Path("db/schema.sql")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")


class LocalEmbedder:
    """Wraps a local sentence-embedding model. Tests use a fake with the same
    `encode` method, so they do not need to download a model."""

    def __init__(self, model_name: str = EMBEDDING_MODEL):
        from sentence_transformers import SentenceTransformer  # slow import, so it is lazy

        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

    def encode(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


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
    embedder = LocalEmbedder()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        by_language = {
            "en": current_translations(conn),
            "ru": current_edition_originals(conn, "ru"),
            "kg": current_edition_originals(conn, "kg"),
        }
        saved = 0
        for lang, rows in by_language.items():
            saved += embed_language(conn, embedder, lang, rows)

        print(f"Saved {saved} embeddings.")


if __name__ == "__main__":
    main()
