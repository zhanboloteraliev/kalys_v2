"""Measure how well the search finds the right article.

Until now the only way to judge a change to search was to ask a question and
read the answer. That cannot tell you whether a change helped or hurt, because
one question is not evidence and nobody remembers how the old version did.

This module runs every question in `eval/questions.jsonl` through the same
hybrid search the chat uses, and compares the articles it returns against the
articles a human labelled as correct. Run it before and after a change and the
numbers say whether the change was an improvement:

    uv run python -m kalys.evaluate

It writes `eval_report.md` and prints a summary. It never calls Bedrock - only
retrieval is measured here, so running it costs nothing.
"""

import json
import os
from pathlib import Path

import psycopg

from kalys.embed import check_embedder_matches, embedder_from_env
from kalys.search import hybrid_search

QUESTIONS_FILE = Path("eval/questions.jsonl")
REPORT_FILE = Path("eval_report.md")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://kalys:kalys@localhost:5432/kalys")

# How deep into the results to look. The chat shows the model the top 10, so
# an article found at rank 11 is one the model never sees.
LIMIT = 10
K_VALUES = (1, 3, 5, 10)

Article = tuple[str, str]  # (act slug, article number)


def load_questions(path: Path = QUESTIONS_FILE) -> list[dict]:
    """Read the labelled questions. One JSON object per line."""
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def act_by_edition(conn) -> dict[int, str]:
    """Map an edition id to the act it belongs to.

    Search returns (edition_id, article number), but a label says "article 97
    of the Criminal Procedure Code". Labelling by act rather than by edition
    means the questions stay correct when a new edition is published.
    """
    rows = conn.execute("SELECT id, act_slug FROM editions").fetchall()
    return {edition_id: act_slug for edition_id, act_slug in rows}


def rank_of_first_hit(results: list[Article], gold: list[Article]) -> int | None:
    """Position of the first correct article, counting from 1, or None.

    This is what Mean Reciprocal Rank is built from: being right at rank 1 is
    worth much more than being right at rank 9, because a reader (and the model
    writing the answer) pays most attention to the top.
    """
    for position, article in enumerate(results, start=1):
        if article in gold:
            return position
    return None


def recall_at_k(results: list[Article], gold: list[Article], k: int) -> float:
    """What share of the correct articles appear in the first `k` results.

    Most questions have one correct article, where this is simply 1.0 or 0.0.
    It matters for the few questions that need two, such as the difference
    between robbery and robbery with violence: finding one of the two is a
    half-answer, and the score should say so.
    """
    if not gold:
        return 0.0
    found = sum(1 for article in gold if article in results[:k])
    return found / len(gold)


def summarise(records: list[dict]) -> dict:
    """Turn per-question results into the numbers that go in the report.

    `hit@k` is the share of questions with at least one correct article in the
    top k - "did the model get to see the right article at all". `recall@10`
    answers "how much of what it needed did it see". `MRR` is the average of
    1/rank, so it rewards putting the right article first rather than tenth.
    """
    total = len(records)
    if total == 0:
        return {"questions": 0}

    summary: dict = {"questions": total}
    for k in K_VALUES:
        hits = sum(1 for r in records if r["found_at"] and r["found_at"] <= k)
        summary[f"hit@{k}"] = hits / total
    summary[f"recall@{LIMIT}"] = sum(r["recall"] for r in records) / total
    summary["mrr"] = sum(1 / r["found_at"] for r in records if r["found_at"]) / total
    return summary


def run(conn, embedder, questions: list[dict]) -> list[dict]:
    """Search for every question and record where the correct articles landed."""
    acts = act_by_edition(conn)
    records = []
    for question in questions:
        hits = hybrid_search(conn, embedder, question["question"], limit=LIMIT)
        results = [(acts.get(edition_id, "?"), number) for edition_id, number in hits]
        gold = [(e["act"], e["article"]) for e in question["expect"]]
        records.append(
            {
                "id": question["id"],
                "question": question["question"],
                "lang": question["lang"],
                "kind": question["kind"],
                "act": gold[0][0],
                "gold": gold,
                "results": results,
                "found_at": rank_of_first_hit(results, gold),
                "recall": recall_at_k(results, gold, LIMIT),
            }
        )
    return records


def table(title: str, groups: dict[str, list[dict]]) -> str:
    """One Markdown table: a row per group, a column per measure."""
    header = ["", "questions", *(f"hit@{k}" for k in K_VALUES), f"recall@{LIMIT}", "MRR"]
    lines = [f"### {title}", "", "| " + " | ".join(header) + " |", "|" + " --- |" * len(header)]
    for name, records in groups.items():
        s = summarise(records)
        cells = [
            name,
            str(s["questions"]),
            *(f"{s[f'hit@{k}']:.2f}" for k in K_VALUES),
            f"{s[f'recall@{LIMIT}']:.2f}",
            f"{s['mrr']:.3f}",
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")
    return "\n".join(lines)


def group_by(records: list[dict], field: str) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for record in records:
        groups.setdefault(record[field], []).append(record)
    return dict(sorted(groups.items()))


def report(records: list[dict]) -> str:
    """The full Markdown report, including the questions that failed."""
    parts = [
        "# Retrieval evaluation",
        "",
        "How often hybrid search puts a correct article in front of the model.",
        "Labels are in `eval/questions.jsonl`. Regenerate with `uv run python -m kalys.evaluate`.",
        "",
        table("Overall", {"all": records}),
        table("By language", group_by(records, "lang")),
        table("By act", group_by(records, "act")),
        table("By kind of question", group_by(records, "kind")),
    ]

    missed = [r for r in records if r["found_at"] is None]
    parts.append(f"### Found nothing correct in the top {LIMIT} ({len(missed)})\n")
    if missed:
        parts.append("| id | lang | question | expected |")
        parts.append("| --- | --- | --- | --- |")
        for r in missed:
            gold = ", ".join(f"{act} {number}" for act, number in r["gold"])
            parts.append(f"| {r['id']} | {r['lang']} | {r['question']} | {gold} |")
    else:
        parts.append("None.")
    parts.append("")

    weak = sorted(
        (r for r in records if r["found_at"] and r["found_at"] > 3),
        key=lambda r: -r["found_at"],
    )
    parts.append(f"### Correct article found, but below rank 3 ({len(weak)})\n")
    if weak:
        parts.append("| id | lang | rank | question | expected |")
        parts.append("| --- | --- | --- | --- | --- |")
        for r in weak:
            gold = ", ".join(f"{act} {number}" for act, number in r["gold"])
            parts.append(
                f"| {r['id']} | {r['lang']} | {r['found_at']} | {r['question']} | {gold} |"
            )
    else:
        parts.append("None.")
    parts.append("")
    return "\n".join(parts)


def main() -> None:
    questions = load_questions()
    embedder = embedder_from_env()
    with psycopg.connect(DATABASE_URL) as conn:
        check_embedder_matches(conn, embedder)
        records = run(conn, embedder, questions)

    REPORT_FILE.write_text(report(records), encoding="utf-8")

    overall = summarise(records)
    print(f"{overall['questions']} questions")
    for k in K_VALUES:
        print(f"  hit@{k}      {overall[f'hit@{k}']:.2f}")
    print(f"  recall@{LIMIT}  {overall[f'recall@{LIMIT}']:.2f}")
    print(f"  MRR        {overall['mrr']:.3f}")
    for lang, group in group_by(records, "lang").items():
        s = summarise(group)
        print(f"  {lang}: hit@{LIMIT} {s[f'hit@{LIMIT}']:.2f}  MRR {s['mrr']:.3f}")
    print(f"\nWrote {REPORT_FILE}")


if __name__ == "__main__":
    main()
