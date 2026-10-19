"""Tests for the retrieval metrics.

These use made-up result lists, so they need no database and no model. The
point is that the metrics themselves are right: if the scoring is wrong, every
number in the report is wrong in a way that is hard to notice.
"""

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
