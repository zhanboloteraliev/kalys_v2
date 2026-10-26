"""Step 4: split each edition's HTML into articles, in Russian and Kyrgyz.

Run:  uv run python -m kalys.parse
Reads: data/raw/manifest.json and the raw GetEdition files it points to
       (made by `kalys.ingest`).
Output: parse_report.md

The Word HTML export writes an "added article" like "Статья 71-1" with a
superscript digit instead of a hyphen, and it writes that superscript in three
different ways (a <sup> tag, a named entity, or rarely a raw unicode character).
We normalize all of them to a plain hyphen before splitting into articles.
See docs/cbd-api.md.
"""

import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from kalys.acts import ACTS

RAW_DIR = Path("data/raw")
MANIFEST_FILE = RAW_DIR / "manifest.json"
HF_LAWS_FILE = Path("data/hf/laws.parquet")
REPORT_FILE = Path("parse_report.md")

SUPERSCRIPT_DIGITS = {"¹": "1", "²": "2", "³": "3"}
BLOCK_TAGS = {"p", "div", "br", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}
SKIP_TAGS = {"style", "script"}

# "Статья 1." or "Статья 71-1." (ru); "1-берене" or "71-1-берене" (kg).
HEADINGS = {
    "ru": re.compile(r"^Статья\s+(\d+(?:-\d+)?)\.?\s*(.*)$"),
    "kg": re.compile(r"^(\d+(?:-\d+)?)-берене\.?\s*(.*)$"),
}

# A few added articles are published without the word "Статья", and with a
# space inside the number: "65 -1. День голосования на выборах" sits inside the
# body of article 65 in the Labour Code. The Kyrgyz text gives them a proper
# heading, so only the Russian side loses them, and the article disappears -
# in that case a paid day off for voting, which people do ask about.
#
# This is accepted as a heading only when the leading number repeats the
# article it was found inside, and that condition is what makes it safe: the
# parts within an article are numbered "1.", "2." and never carry the article's
# own number, so nothing else can match.
CONTINUATION_HEADING = re.compile(r"^(\d+)\s*-\s*(\d+)\.\s+(\S.*)$")


class _BodyTextExtractor(HTMLParser):
    """Turns the Word HTML export into plain text, one paragraph per line."""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip_depth += 1
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "sup":
            self.parts.append("-")  # Статья 71<sup>1</sup> -> Статья 71-1

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)

    def handle_data(self, data):
        if not self._skip_depth:
            self.parts.append(data)


def extract_body_text(content: str) -> str:
    """The GetEdition HTML, as plain text with one paragraph per line."""
    content = html.unescape(content)
    for char, digit in SUPERSCRIPT_DIGITS.items():
        content = content.replace(char, "-" + digit)
    body_start = content.find("<body")
    if body_start != -1:
        content = content[body_start:]
    extractor = _BodyTextExtractor()
    extractor.feed(content)
    text = "".join(extractor.parts).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


@dataclass(frozen=True)
class Article:
    number: str  # e.g. "71-1"
    title: str  # empty for acts (like the Constitution) that have no article titles
    text: str


def parse_articles(content: str, lang: str) -> list[Article]:
    """Split one edition's HTML into articles. lang is "ru" or "kg"."""
    heading = HEADINGS[lang]
    lines = extract_body_text(content).split("\n")
    starts = [(i, m) for i, line in enumerate(lines) if (m := heading.match(line))]

    articles = []
    for position, (line_index, match) in enumerate(starts):
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        body_lines = [line for line in lines[line_index + 1 : end] if line]
        articles.extend(split_continuations(match.group(1), match.group(2), body_lines))
    return articles


def split_continuations(number: str, title: str, body_lines: list[str]) -> list[Article]:
    """One article, plus any added article buried in its body without a heading.

    See CONTINUATION_HEADING. Returns the article unchanged when there is
    nothing to split, which is the usual case.
    """
    base = number.partition("-")[0]
    cuts = [
        (index, match)
        for index, line in enumerate(body_lines)
        if (match := CONTINUATION_HEADING.match(line)) and match.group(1) == base
    ]
    if not cuts:
        return [Article(number=number, title=title, text="\n".join(body_lines))]

    articles = [Article(number=number, title=title, text="\n".join(body_lines[: cuts[0][0]]))]
    for position, (index, match) in enumerate(cuts):
        end = cuts[position + 1][0] if position + 1 < len(cuts) else len(body_lines)
        articles.append(
            Article(
                number=f"{match.group(1)}-{match.group(2)}",
                title=match.group(3).strip(),
                text="\n".join(body_lines[index + 1 : end]),
            )
        )
    return articles


def _number_sort_key(number: str) -> tuple[int, int]:
    base, _, suffix = number.partition("-")
    return (int(base), int(suffix) if suffix else 0)


@dataclass(frozen=True)
class MatchedArticle:
    number: str
    ru: Article | None
    kg: Article | None


def match_articles(ru_articles: list[Article], kg_articles: list[Article]) -> list[MatchedArticle]:
    """Pair up ru and kg articles of the same edition by article number."""
    ru_by_number = {article.number: article for article in ru_articles}
    kg_by_number = {article.number: article for article in kg_articles}
    numbers = sorted(set(ru_by_number) | set(kg_by_number), key=_number_sort_key)
    return [
        MatchedArticle(number, ru_by_number.get(number), kg_by_number.get(number))
        for number in numbers
    ]


def edition_content(row: dict) -> str:
    answer = json.loads((RAW_DIR / row["file"]).read_text(encoding="utf-8"))
    return answer.get("contentKg") or answer.get("contentRu") or ""


def hf_article_counts() -> dict[str, int]:
    """article_count from the Hugging Face dataset, keyed by our act slug."""
    if not HF_LAWS_FILE.exists():
        return {}
    import pyarrow.parquet as pq

    laws = pq.read_table(HF_LAWS_FILE).to_pylist()
    by_identifier = {row["identifier"]: row["article_count"] for row in laws}
    counts = {}
    for act in ACTS:
        if act.document_code in by_identifier:
            counts[act.slug] = by_identifier[act.document_code]
    return counts


def make_report(rows: list[dict]) -> str:
    lines = ["# Parse report", ""]
    hf_counts = hf_article_counts()

    for act in ACTS:
        act_rows = [row for row in rows if row["act"] == act.slug]
        if not act_rows:
            lines.append(f"## {act.slug}: NOT DOWNLOADED")
            continue
        # Edition ids are not in date order (see docs/cbd-api.md), but the manifest
        # lists them in the order GetDocument gave them: oldest to newest.
        edition_ids = list(dict.fromkeys(row["edition_id"] for row in act_rows))
        lines.append(f"## {act.slug}: {len(edition_ids)} editions")

        total_mismatches = 0
        newest_edition_id = newest_matched = None
        for edition_id in edition_ids:
            by_lang = {row["lang"]: row for row in act_rows if row["edition_id"] == edition_id}
            ru_articles = parse_articles(edition_content(by_lang["ru"]), "ru")
            kg_articles = parse_articles(edition_content(by_lang["kg"]), "kg")
            matched = match_articles(ru_articles, kg_articles)
            mismatches = [m for m in matched if m.ru is None or m.kg is None]
            total_mismatches += len(mismatches)
            # manifest.json lists editions oldest to newest, so the last one wins.
            newest_edition_id, newest_matched = edition_id, matched

        ru_count = sum(1 for m in newest_matched if m.ru is not None)
        kg_count = sum(1 for m in newest_matched if m.kg is not None)
        newest_mismatches = sum(1 for m in newest_matched if m.ru is None or m.kg is None)
        lines.append(
            f"- newest edition ({newest_edition_id}): {ru_count} ru articles, "
            f"{kg_count} kg articles, {newest_mismatches} numbers missing in one language"
        )
        lines.append(f"- all editions: {total_mismatches} missing-in-one-language numbers in total")
        if act.slug in hf_counts:
            lines.append(
                f"- Hugging Face article_count for the current edition: {hf_counts[act.slug]} "
                f"(ours: {ru_count} ru / {kg_count} kg)"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> None:
    rows = json.loads(MANIFEST_FILE.read_text(encoding="utf-8"))
    REPORT_FILE.write_text(make_report(rows), encoding="utf-8")
    print(f"Report saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
