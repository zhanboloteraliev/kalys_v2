"""Tests for the retrieval metrics.

These use made-up result lists, so they need no database and no model. The
point is that the metrics themselves are right: if the scoring is wrong, every
number in the report is wrong in a way that is hard to notice.
"""

from kalys import evaluate
from kalys.evaluate import rank_of_first_hit, recall_at_k, summarise

A = ("criminal_procedure_code", "97")
B = ("criminal_procedure_code", "102")
X = ("criminal_code", "205")
Y = ("constitution", "13")


def test_rank_of_first_hit_counts_from_one():
    assert rank_of_first_hit([A, X, Y], [A]) == 1
    assert rank_of_first_hit([X, Y, A], [A]) == 3


def test_rank_of_first_hit_is_none_when_absent():
    assert rank_of_first_hit([X, Y], [A]) is None


def test_rank_of_first_hit_takes_the_earliest_of_several_correct_articles():
    assert rank_of_first_hit([X, B, A], [A, B]) == 2


def test_recall_counts_the_share_of_correct_articles_found():
    assert recall_at_k([A, B], [A, B], 10) == 1.0
    assert recall_at_k([A, X], [A, B], 10) == 0.5
    assert recall_at_k([X, Y], [A, B], 10) == 0.0


def test_recall_only_looks_at_the_first_k_results():
    assert recall_at_k([X, Y, A], [A], 2) == 0.0
    assert recall_at_k([X, Y, A], [A], 3) == 1.0


def test_recall_is_zero_when_nothing_is_labelled():
    assert recall_at_k([A], [], 10) == 0.0


def _record(found_at, recall=1.0):
    return {"found_at": found_at, "recall": recall}


def test_summarise_hit_rates_and_mrr():
    # Ranks 1, 2 and nothing found: two of three questions are answered,
    # and MRR is (1/1 + 1/2 + 0) / 3.
    summary = summarise([_record(1), _record(2), _record(None, 0.0)])
    assert summary["questions"] == 3
    assert summary["hit@1"] == 1 / 3
    assert summary["hit@3"] == 2 / 3
    assert summary["mrr"] == (1 + 0.5) / 3
    assert summary["recall@10"] == 2 / 3


def test_summarise_hit_at_k_ignores_results_below_k():
    summary = summarise([_record(5)])
    assert summary["hit@3"] == 0.0
    assert summary["hit@5"] == 1.0


def test_summarise_of_nothing_does_not_divide_by_zero():
    assert summarise([]) == {"questions": 0}


# --- Step 20e: the harness must measure the pipeline that ships ---------------
#
# These use a fake search and a fake router, so they still need no database and
# no model. What they check is the wiring: that the question the search gets is
# the routed one, and that the report would show a Russian question being
# treated as Kyrgyz - the bug that shipped in Step 19 while every test passed.


def _questions():
    return [
        {
            "id": "q1",
            "question": "Шектүү адамдын кандай укуктары бар?",
            "lang": "ky",
            "kind": "citizen",
            "expect": [{"act": "criminal_procedure_code", "article": "44"}],
        },
        {
            "id": "q2",
            "question": "What rights does a suspect have?",
            "lang": "en",
            "kind": "citizen",
            "expect": [{"act": "criminal_procedure_code", "article": "44"}],
        },
    ]


def _fake_search(searched_questions):
    """A search that records what it was asked and always finds article 44."""

    def search(conn, embedder, question, limit):
        searched_questions.append(question)
        return [(1, "44")]

    return search


def test_run_searches_with_the_routed_question(monkeypatch):
    searched: list[str] = []
    monkeypatch.setattr(evaluate, "hybrid_search", _fake_search(searched))
    monkeypatch.setattr(evaluate, "act_by_edition", lambda conn: {1: "criminal_procedure_code"})

    def route(question):
        if question.startswith("Шектүү"):
            return "Какие права есть у подозреваемого?", True
        return question, False

    records = evaluate.run(None, None, _questions(), route)

    # The Kyrgyz question reached the search as Russian; English untouched.
    assert searched == ["Какие права есть у подозреваемого?", "What rights does a suspect have?"]
    assert records[0]["question"].startswith("Шектүү")  # still labelled by what was asked
    assert records[0]["searched"] == "Какие права есть у подозреваемого?"
    assert records[0]["routed"] is True
    assert records[1]["routed"] is False


def test_run_without_a_router_searches_the_question_as_asked(monkeypatch):
    # KALYS_EVAL_ROUTE=0: retrieval only, no Bedrock, no cost.
    searched: list[str] = []
    monkeypatch.setattr(evaluate, "hybrid_search", _fake_search(searched))
    monkeypatch.setattr(evaluate, "act_by_edition", lambda conn: {1: "criminal_procedure_code"})

    records = evaluate.run(None, None, _questions(), None)

    assert searched == [q["question"] for q in _questions()]
    assert [r["routed"] for r in records] == [False, False]


def test_routing_table_counts_translations_per_language():
    records = [
        {"lang": "ky", "routed": True},
        {"lang": "ky", "routed": True},
        {"lang": "ru", "routed": False},
        {"lang": "en", "routed": False},
    ]

    text = evaluate.routing(records)

    assert "| ky | 2 | 2 |" in text
    assert "| ru | 1 | 0 |" in text
    assert "| en | 1 | 0 |" in text


def test_routing_table_shows_a_russian_question_treated_as_kyrgyz():
    # The Step 19 bug, as it would now appear in the report: a non-zero count
    # on a row that is not `ky`.
    records = [{"lang": "ru", "routed": True}, {"lang": "ru", "routed": False}]

    assert "| ru | 2 | 1 |" in evaluate.routing(records)
