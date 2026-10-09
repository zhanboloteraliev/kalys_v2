"""Step 6: hybrid search over the current edition's articles.

Combines two ways of finding relevant articles for a question:
- keyword search: Postgres full-text search on the article text (good at
  exact legal terms, e.g. "necessary defense");
- semantic search: cosine similarity between sentence embeddings (good at
  matching meaning even when the wording differs).

Both searches run in all three languages (en/ru/kg), not just the
question's own language. We tried detecting the question's language first
and searching only that one language's text, but a short question that
happens to use none of the three Kyrgyz-only letters gets misread as
Russian - and since embeddings are now split by language (see
kalys.embed), a wrong guess sent the whole search to the wrong language's
embeddings and broke it completely, which is worse than the slower,
always-correct approach of just searching every language. See the Step 6
entry in LEARNING.md.

All six ranked lists (2 search methods x 3 languages) are merged with
Reciprocal Rank Fusion (RRF): an article that several lists agree on beats
one only one list liked, so the question's actual language naturally
dominates without us having to guess it first.

Run:
  docker compose up -d db
  uv run python -m kalys.embed          (once, after kalys.translate)
  uv run python -m kalys.search "how is a child questioned as a suspect?"
"""

import os
import sys
from pathlib import Path

import psycopg

from kalys.embed import LocalEmbedder, vector_literal

SCHEMA_FILE = Path("db/schema.sql")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")
TOP_N = 10
RRF_K = 60  # a standard RRF constant: big enough that rank 1 vs. rank 2 is not everything
LANGUAGES = ("en", "ru", "kg")
TEXT_SEARCH_CONFIG = {"en": "english", "ru": "russian", "kg": "simple"}


def keyword_search(conn, query: str, lang: str, limit: int = TOP_N) -> list[tuple[int, str]]:
    """Articles in `lang` whose text matches the query's words, best first."""
    config = TEXT_SEARCH_CONFIG[lang]
    if lang == "en":
        rows = conn.execute(
            """
            SELECT edition_id, number FROM translations
            WHERE ts @@ websearch_to_tsquery(%s::regconfig, %s)
            ORDER BY ts_rank(ts, websearch_to_tsquery(%s::regconfig, %s)) DESC
            LIMIT %s
            """,
            (config, query, config, query, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT ar.edition_id, ar.number
            FROM articles ar
            JOIN editions e ON ar.edition_id = e.id
            WHERE ar.lang = %s
              AND e.edition_code = (
                  SELECT max(edition_code) FROM editions WHERE act_slug = e.act_slug
              )
              AND ar.ts @@ websearch_to_tsquery(%s::regconfig, %s)
            ORDER BY ts_rank(ar.ts, websearch_to_tsquery(%s::regconfig, %s)) DESC
            LIMIT %s
            """,
            (lang, config, query, config, query, limit),
        ).fetchall()
    return [(edition_id, number) for edition_id, number in rows]


def vector_search(
    conn, vector: list[float], lang: str, limit: int = TOP_N
) -> list[tuple[int, str]]:
    """Articles in `lang` whose embedding is closest in meaning to `vector`."""
    rows = conn.execute(
        "SELECT edition_id, number FROM embeddings "
        "WHERE lang = %s ORDER BY embedding <=> %s::vector LIMIT %s",
        (lang, vector_literal(vector), limit),
    ).fetchall()
    return [(edition_id, number) for edition_id, number in rows]


def reciprocal_rank_fusion(
    rankings: list[list[tuple[int, str]]], k: int = RRF_K
) -> list[tuple[int, str]]:
    """Merge several ranked lists of the same kind of item into one ranking.

    Each list gives its items a score based on rank (1st place scores more
    than 10th place). Scores from every list are added up, so an item near
    the top of more than one list rises above one that only one list liked.
    """
    scores: dict[tuple[int, str], float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return [key for key, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]


def hybrid_search(conn, embedder, query: str, limit: int = TOP_N) -> list[tuple[int, str]]:
    [vector] = embedder.encode([query])
    rankings = []
    for lang in LANGUAGES:
        rankings.append(keyword_search(conn, query, lang, limit=limit))
        rankings.append(vector_search(conn, vector, lang, limit=limit))
    return reciprocal_rank_fusion(rankings)[:limit]


def describe(conn, edition_id: int, number: str) -> str:
    """One line for a search result: act name, article number, edition date, title."""
    row = conn.execute(
        """
        SELECT a.name_en, e.edition_date, t.title
        FROM translations t
        JOIN editions e ON e.id = t.edition_id
        JOIN acts a ON a.slug = e.act_slug
        WHERE t.edition_id = %s AND t.number = %s
        """,
        (edition_id, number),
    ).fetchone()
    if row is None:  # a kg-only article (see LEARNING.md, Step 4) has no English translation
        return f"Article {number} (edition {edition_id}): no English translation yet"
    name_en, edition_date, title = row
    return f"{name_en}, Article {number} ({edition_date}): {title}"


def main() -> None:
    query = " ".join(sys.argv[1:]) or input("Search: ")
    embedder = LocalEmbedder()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))
        for edition_id, number in hybrid_search(conn, embedder, query):
            print(describe(conn, edition_id, number))


if __name__ == "__main__":
    main()
