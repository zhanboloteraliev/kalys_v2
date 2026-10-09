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


def embedding_text(title: str, text: str) -> str:
    """The text we embed for one article: its title (if any) and body."""
    return f"{title}\n\n{text}".strip()


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
    conn, edition_id: int, number: str, lang: str, vector: list[float], model: str
) -> None:
    conn.execute(
        "INSERT INTO embeddings (edition_id, number, lang, embedding, model) "
        "VALUES (%s, %s, %s, %s::vector, %s) "
        "ON CONFLICT (edition_id, number, lang) DO UPDATE SET "
        "embedding = EXCLUDED.embedding, model = EXCLUDED.model, created_at = now()",
        (edition_id, number, lang, vector_literal(vector), model),
    )


def embed_language(conn, embedder, lang: str, rows: list[tuple[int, str, str, str]]) -> None:
    print(f"Embedding {len(rows)} {lang} articles with {embedder.model_name} ...")
    texts = [embedding_text(title, text) for _, _, title, text in rows]
    vectors = embedder.encode(texts)
    for (edition_id, number, _, _), vector in zip(rows, vectors, strict=True):
        save_embedding(conn, edition_id, number, lang, vector, embedder.model_name)


def main() -> None:
    embedder = LocalEmbedder()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        by_language = {
            "en": current_translations(conn),
            "ru": current_edition_originals(conn, "ru"),
            "kg": current_edition_originals(conn, "kg"),
        }
        for lang, rows in by_language.items():
            embed_language(conn, embedder, lang, rows)

        print(f"Saved {sum(len(rows) for rows in by_language.values())} embeddings.")


if __name__ == "__main__":
    main()
