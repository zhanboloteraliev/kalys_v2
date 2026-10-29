"""Step 4: load parsed articles into PostgreSQL.

Run:
  docker compose up -d db
  uv run python -m kalys.load_db

Reads data/raw/manifest.json (made by `kalys.ingest`) and the raw files it
points to, parses them with `kalys.parse`, and loads acts/editions/articles
into Postgres. Safe to run again: existing rows are left as they are.
"""

import json
import os
from pathlib import Path

import psycopg

from kalys.acts import ACTS
from kalys.parse import RAW_DIR, edition_content, parse_articles

MANIFEST_FILE = RAW_DIR / "manifest.json"
SCHEMA_FILE = Path("db/schema.sql")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")


def main() -> None:
    rows = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))

    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        for act in ACTS:
            conn.execute(
                "INSERT INTO acts (slug, document_code, name_en) VALUES (%s, %s, %s) "
                "ON CONFLICT (slug) DO NOTHING",
                (act.slug, act.document_code, act.name_en),
            )

        editions_loaded = set()
        articles_loaded = 0
        for row in rows:
            if row["edition_id"] not in editions_loaded:
                conn.execute(
                    "INSERT INTO editions (id, act_slug, edition_code, edition_date, label) "
                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING",
                    (
                        row["edition_id"],
                        row["act"],
                        row["edition_code"],
                        row["edition_date"],
                        row["edition_label"],
                    ),
                )
                editions_loaded.add(row["edition_id"])

            for article in parse_articles(edition_content(row), row["lang"]):
                conn.execute(
                    "INSERT INTO articles (edition_id, lang, number, title, text) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    # DO UPDATE, not DO NOTHING: the articles table is derived
                    # from the cached source files, so re-running the loader
                    # after a parser fix must converge on the same result. With
                    # DO NOTHING, a fix that split one article into two inserted
                    # the new article but left the old one with its stale text,
                    # so the same text was stored twice (Labour Code 65/65-1).
                    "ON CONFLICT (edition_id, lang, number) DO UPDATE "
                    "SET title = EXCLUDED.title, text = EXCLUDED.text",
                    (row["edition_id"], row["lang"], article.number, article.title, article.text),
                )
                articles_loaded += 1

        print(f"Loaded {len(editions_loaded)} editions, {articles_loaded} article rows.")


if __name__ == "__main__":
    main()
