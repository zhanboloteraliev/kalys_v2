"""Step 5 (manual path): export the current edition's Russian articles to
plain text files, to paste into a Claude.ai chat or Project by hand.

Used because the free Gemini API tier was too overloaded to finish ~1,100
articles in reasonable time - see LEARNING.md. This path trades automation
for using a Claude Pro subscription instead of paying for API usage.

Run:
  docker compose up -d db   (if not already running)
  uv run python -m kalys.export_for_translation
Output: data/manual_translate/<act_slug>_ru.txt, one file per act.
"""

import os
from pathlib import Path

import psycopg

from kalys.acts import ACTS

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")
OUT_DIR = Path("data/manual_translate")


def sort_key(number: str) -> tuple[int, int]:
    base, _, suffix = number.partition("-")
    return (int(base), int(suffix) if suffix else 0)


def current_edition_articles(conn, act_slug: str) -> list[tuple[str, str, str]]:
    """(number, title, text) of the ru articles in the newest edition of one act."""
    rows = conn.execute(
        """
        SELECT ar.number, ar.title, ar.text
        FROM articles ar
        JOIN editions e ON ar.edition_id = e.id
        WHERE ar.lang = 'ru' AND e.act_slug = %s
          AND e.edition_code = (SELECT max(edition_code) FROM editions WHERE act_slug = %s)
        """,
        (act_slug, act_slug),
    ).fetchall()
    return sorted(rows, key=lambda row: sort_key(row[0]))


def format_article_block(number: str, title: str, text: str) -> str:
    heading = f"Статья {number}. {title}".strip()
    return f"=== ARTICLE {number} ===\n{heading}\n\n{text}\n"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        for act in ACTS:
            rows = current_edition_articles(conn, act.slug)
            blocks = [format_article_block(number, title, text) for number, title, text in rows]
            out_file = OUT_DIR / f"{act.slug}_ru.txt"
            out_file.write_text("\n".join(blocks), encoding="utf-8")
            print(f"{act.slug}: wrote {len(rows)} articles to {out_file}")


if __name__ == "__main__":
    main()
