"""Step 5: translate the current edition of each act into English, once.

Run:
  echo "GEMINI_API_KEY=..." > .env   (free key from Google AI Studio, gitignored)
  docker compose up -d db            (if not already running)
  uv run python -m kalys.load_db     (if not already loaded)
  uv run python -m kalys.translate
Output: translate_report.md

Reads the Russian articles of the newest edition of each act from Postgres
and translates each one with the Gemini API (the free "Flash" tier - see
docs/cbd-api.md and LEARNING.md for why Gemini instead of a paid API). Every
API answer is cached on disk, so re-running this script never pays for (or
waits on) the same article twice.

English text is a machine translation, made once and reused - never a
substitute for the Russian or Kyrgyz original. Always show "Machine
translation. Not an official text." next to it when displaying it. See
CLAUDE.md's data rules.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import anthropic
import psycopg
from dotenv import load_dotenv
from google import genai

load_dotenv()  # reads .env in the project root, if it exists; never overwrites a real env var

GEMINI_MODEL = "gemini-3.8-flash"
CLAUDE_MODEL = "claude-sonnet-5"
MAX_WORKERS = 3  # Gemini's free tier has a low requests-per-minute limit
GEMINI_MAX_RETRIES = 5  # waits 4, 8, 16, 32, 64 seconds on a rate-limit error
CACHE_DIR = Path("data/cache/translate")
SCHEMA_FILE = Path("db/schema.sql")
REPORT_FILE = Path("translate_report.md")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")

SYSTEM_PROMPT = (
    "You translate Kyrgyz legal texts from Russian into English, for a legal "
    "search tool. Keep the legal meaning exact and the structure the same "
    "(numbered points, paragraph breaks). Do not add commentary or notes. "
    'Reply with only one JSON object: {"title": "...", "text": "..."}. '
    'The first line of the input is "Статья <number>." optionally followed by '
    'a title. If nothing follows the number (the line is just "Статья <number>." '
    'with nothing after it), the article has no title: return "" for "title" - '
    "do not invent one, and do not use the number as a title."
)


@dataclass(frozen=True)
class Article:
    edition_id: int
    number: str
    title: str
    text: str


def build_prompt(article: Article) -> str:
    return f"Статья {article.number}. {article.title}\n\n{article.text}".strip()


def parse_translation(response_text: str) -> dict:
    """Pull {"title": ..., "text": ...} out of the model's reply."""
    match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if match is None:
        raise ValueError(f"No JSON object in the model's reply: {response_text!r}")
    data = json.loads(match.group(0))
    return {"title": data.get("title", ""), "text": data["text"]}


class ClaudeTranslator:
    """Wraps the Anthropic API. Tests use a fake with the same `ask` method."""

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = CLAUDE_MODEL):
        self.client = client or anthropic.Anthropic()
        self.model = model

    def ask(self, prompt: str) -> str:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text


class GeminiTranslator:
    """Wraps the Gemini API (the free tier). Needs the GEMINI_API_KEY env var."""

    def __init__(self, client=None, model: str = GEMINI_MODEL, sleep=time.sleep):
        self.client = client or genai.Client()
        self.model = model
        self.sleep = sleep  # a parameter so tests can run without real waiting

    def ask(self, prompt: str) -> str:
        for attempt in range(GEMINI_MAX_RETRIES + 1):
            try:
                interaction = self.client.interactions.create(
                    model=self.model,
                    system_instruction=SYSTEM_PROMPT,
                    input=prompt,
                    generation_config={"max_output_tokens": 4096},
                )
                return interaction.output_text
            except Exception as error:
                # 429 = rate limited, 5xx = the model is temporarily overloaded.
                # Both are worth retrying; a 4xx like "bad request" is not.
                status = getattr(error, "status_code", None)
                is_retryable = status == 429 or (status is not None and 500 <= status < 600)
                if not is_retryable or attempt == GEMINI_MAX_RETRIES:
                    raise
                self.sleep(2 ** (attempt + 2))
        raise RuntimeError("unreachable")


def translate_article(translator, article: Article, cache_dir: Path = CACHE_DIR) -> dict:
    """Translate one article. Cached on disk, so we never pay for (or wait on) it twice."""
    cache_file = cache_dir / f"{article.edition_id}_{article.number}_{translator.model}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))

    translation = parse_translation(translator.ask(build_prompt(article)))

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(translation, ensure_ascii=False, indent=2), encoding="utf-8")
    return translation


def current_edition_articles(conn) -> list[Article]:
    """The ru articles of the newest edition of each act (highest edition_code)."""
    rows = conn.execute(
        """
        SELECT ar.edition_id, ar.number, ar.title, ar.text
        FROM articles ar
        JOIN editions e ON ar.edition_id = e.id
        WHERE ar.lang = 'ru'
          AND e.edition_code = (
              SELECT max(edition_code) FROM editions WHERE act_slug = e.act_slug
          )
        ORDER BY e.act_slug, ar.number
        """
    ).fetchall()
    return [Article(*row) for row in rows]


def make_report(results: list[tuple[Article, dict | None, str | None]]) -> str:
    ok = [article for article, translation, _ in results if translation is not None]
    failed = [(article, error) for article, translation, error in results if translation is None]

    lines = ["# Translate report", "", f"Translated {len(ok)} of {len(results)} articles."]
    if failed:
        lines.append("")
        lines.append("Failed:")
        for article, error in failed:
            lines.append(f"- edition {article.edition_id}, article {article.number}: {error}")
    lines.append("")
    lines.append('Remember: always show "Machine translation. Not an official text."')
    lines.append("next to this text when it is displayed.")
    return "\n".join(lines) + "\n"


def already_translated(conn) -> set[tuple[int, str]]:
    rows = conn.execute("SELECT edition_id, number FROM translations").fetchall()
    return {(edition_id, number) for edition_id, number in rows}


def save_translation(conn, article: Article, translation: dict, model: str) -> None:
    # Written right away, not batched until the end: a long, flaky batch like
    # this one should never lose progress if it gets interrupted.
    conn.execute(
        "INSERT INTO translations (edition_id, number, source_lang, title, text, model) "
        "VALUES (%s, %s, 'ru', %s, %s, %s) ON CONFLICT (edition_id, number) DO NOTHING",
        (article.edition_id, article.number, translation["title"], translation["text"], model),
    )


def main() -> None:
    translator = GeminiTranslator()
    with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))

        done = already_translated(conn)
        all_articles = current_edition_articles(conn)
        articles = [a for a in all_articles if (a.edition_id, a.number) not in done]
        print(f"{len(done)} articles already translated, {len(articles)} left to do.")

        results = []
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {pool.submit(translate_article, translator, a): a for a in articles}
            for future, article in futures.items():
                try:
                    translation = future.result()
                except Exception as error:  # one bad article should not stop the batch
                    results.append((article, None, str(error)))
                    print(f"FAILED: article {article.number} ({article.edition_id}): {error}")
                    continue

                save_translation(conn, article, translation, translator.model)
                results.append((article, translation, None))
                print(f"{len(results)}/{len(articles)}: translated article {article.number}")

    REPORT_FILE.write_text(make_report(results), encoding="utf-8")
    print(f"Report saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
