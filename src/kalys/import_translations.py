"""Step 5 (manual path): load translations pasted back from a Claude.ai chat.

Pair to kalys.export_for_translation. Reads data/manual_translate/<act>_en.txt
(the files you save the chat's replies into, in the "=== ARTICLE N ==="
format you were asked to use) and loads them into the `translations` table.

Unlike kalys.load_db, this overwrites on conflict (ON CONFLICT ... DO UPDATE):
these files are hand-assembled from a chat, so re-running this after fixing a
pasted reply should replace the old row, not silently keep it.

Run:
  docker compose up -d db   (if not already running)
  uv run python -m kalys.import_translations
"""

import os
import re
from pathlib import Path

import psycopg

from kalys.acts import ACTS

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")
IN_DIR = Path("data/manual_translate")
MODEL_LABEL = "claude.ai (manual, Pro plan)"

ARTICLE_BLOCK = re.compile(
    r"=== ARTICLE (?P<number>\S+) ===\s*"
    r"TITLE:(?P<title>.*?)\n"
    r"TEXT:\s*(?P<text>.*?)(?=\n=== ARTICLE |\Z)",
    re.DOTALL,
)


def parse_translations(reply_text: str) -> list[tuple[str, str, str]]:
    """(number, title, text) for every article block in one reply file."""
    matches = ARTICLE_BLOCK.finditer(reply_text)
    return [(m["number"].strip(), m["title"].strip(), m["text"].strip()) for m in matches]


def current_edition_id(conn, act_slug: str) -> int:
    row = conn.execute(
        "SELECT id FROM editions WHERE act_slug = %s ORDER BY edition_code DESC LIMIT 1",
        (act_slug,),
    ).fetchone()
    return row[0]


def main() -> None:
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        for act in ACTS:
            in_file = IN_DIR / f"{act.slug}_en.txt"
            if not in_file.exists():
                print(f"{act.slug}: skipping, {in_file} does not exist yet")
                continue

            edition_id = current_edition_id(conn, act.slug)
            translations = parse_translations(in_file.read_text(encoding="utf-8"))
            for number, title, text in translations:
                conn.execute(
                    "INSERT INTO translations "
                    "(edition_id, number, source_lang, title, text, model) "
                    "VALUES (%s, %s, 'ru', %s, %s, %s) "
                    "ON CONFLICT (edition_id, number) DO UPDATE SET "
                    "title = EXCLUDED.title, text = EXCLUDED.text, model = EXCLUDED.model, "
                    "translated_at = now()",
                    (edition_id, number, title, text, MODEL_LABEL),
                )
            print(f"{act.slug}: loaded {len(translations)} translations from {in_file}")


if __name__ == "__main__":
    main()
