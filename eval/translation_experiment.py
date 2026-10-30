"""Does translating a Kyrgyz question before searching help, and by how much?

Kyrgyz retrieval scores far below English and Russian (see the Step 12 entry in
LEARNING.md). Both halves of the hybrid search fail for it: PostgreSQL has no
Kyrgyz stemmer, so keyword search matches whole words only while Kyrgyz builds
meaning with suffixes; and the embedding model places Kyrgyz badly in the
shared vector space.

This script tests one possible fix - translate the question first, then search
- and measures what each option is worth:

    uv run python eval/translation_experiment.py

The translations in `ky_query_translations.json` were made by the assistant
in-session, so this measures the ceiling of the approach rather than what a
cheap runtime translator would manage.

Still useful after Step 15 shipped the runtime version, because the two
numbers answer different questions: `kalys.evaluate` now measures what the
real model's translation achieves, and this measures what a perfect
translation would. The gap between them is what better prompting could still
win.

It embeds with whatever `embedder_from_env` selects, which must be the model
the stored vectors came from - it used to hard-code `LocalEmbedder`, which
Step 14 replaced, so it measured against an index that no longer exists.
"""

import json
from pathlib import Path

import psycopg

from kalys.embed import embedder_from_env
from kalys.evaluate import DATABASE_URL, act_by_edition, load_questions, rank_of_first_hit
from kalys.search import hybrid_search, reciprocal_rank_fusion

TRANSLATIONS_FILE = Path("eval/ky_query_translations.json")
LIMIT = 10
K_VALUES = (1, 3, 5, 10)


def score(ranks: list[int | None]) -> dict:
    """hit@k and MRR from a list of ranks, where None means "never found"."""
    total = len(ranks)
    found = [r for r in ranks if r is not None]
    summary = {f"hit@{k}": sum(1 for r in found if r <= k) / total for k in K_VALUES}
    summary["mrr"] = sum(1 / r for r in found) / total
    return summary


def variants(question: str, russian: str, english: str) -> dict[str, list[str]]:
    """The query or queries each strategy would send to the search."""
    return {
        "ky only (today)": [question],
        "ru translation only": [russian],
        "en translation only": [english],
        "ky + ru fused": [question, russian],
        "ky + ru + en fused": [question, russian, english],
    }


def search_with(conn, embedder, acts: dict[int, str], queries: list[str]) -> list[tuple[str, str]]:
    """Search once per query and merge the rankings, as the chat already does."""
    rankings = [hybrid_search(conn, embedder, query, limit=LIMIT) for query in queries]
    merged = rankings[0] if len(rankings) == 1 else reciprocal_rank_fusion(rankings)
    return [(acts.get(edition_id, "?"), number) for edition_id, number in merged[:LIMIT]]


def main() -> None:
    translations = json.loads(TRANSLATIONS_FILE.read_text(encoding="utf-8"))
    questions = [q for q in load_questions() if q["lang"] == "ky"]
    embedder = embedder_from_env()

    with psycopg.connect(DATABASE_URL) as conn:
        acts = act_by_edition(conn)
        ranks_by_variant: dict[str, list[int | None]] = {}
        for question in questions:
            translated = translations[question["id"]]
            gold = [(e["act"], e["article"]) for e in question["expect"]]
            for name, queries in variants(
                question["question"], translated["ru"], translated["en"]
            ).items():
                results = search_with(conn, embedder, acts, queries)
                ranks_by_variant.setdefault(name, []).append(rank_of_first_hit(results, gold))

    columns = " ".join(f"{f'hit@{k}':>6}" for k in K_VALUES)
    print(f"{len(questions)} Kyrgyz questions\n")
    print(f"{'query sent to the search':<24} {columns} {'MRR':>7}")
    for name, ranks in ranks_by_variant.items():
        s = score(ranks)
        cells = " ".join(f"{s[f'hit@{k}']:>6.2f}" for k in K_VALUES)
        print(f"{name:<24} {cells} {s['mrr']:>7.3f}")

    print("\nrank of the first correct article ('-' = not in the top 10)")
    print(f"{'id':<6} {'ky':>4} {'ru':>4} {'en':>4}  question")
    for i, question in enumerate(questions):

        def rank(name: str, index: int = i) -> str:
            return str(ranks_by_variant[name][index] or "-")

        print(
            f"{question['id']:<6} {rank('ky only (today)'):>4} "
            f"{rank('ru translation only'):>4} {rank('en translation only'):>4}  "
            f"{question['question'][:52]}"
        )


if __name__ == "__main__":
    main()
