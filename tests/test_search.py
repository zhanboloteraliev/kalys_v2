from kalys.search import reciprocal_rank_fusion


def test_item_ranked_first_in_both_lists_wins():
    keyword = [(1, "5"), (1, "6")]
    vector = [(1, "5"), (1, "7")]

    result = reciprocal_rank_fusion([keyword, vector])

    assert result[0] == (1, "5")


def test_item_near_top_of_both_lists_beats_item_top_of_only_one():
    # (1, "9") is 2nd in both lists; (1, "1") is 1st in only the keyword list.
    keyword = [(1, "1"), (1, "9")]
    vector = [(1, "2"), (1, "9")]

    result = reciprocal_rank_fusion([keyword, vector])

    assert result[0] == (1, "9")


def test_item_missing_from_one_list_is_still_included():
    keyword = [(1, "5")]
    vector: list[tuple[int, str]] = []

    result = reciprocal_rank_fusion([keyword, vector])

    assert result == [(1, "5")]


def test_empty_rankings_give_an_empty_result():
    assert reciprocal_rank_fusion([[], []]) == []


def test_small_k_favors_a_single_top_rank_over_two_mediocre_ranks():
    # (1, "A") is 1st in one list only; (1, "B") is 5th in both lists.
    # With a small k, being 1st once outweighs being 5th twice.
    ranking_one = [(1, "A"), (1, "X1"), (1, "X2"), (1, "X3"), (1, "B")]
    ranking_two = [(1, "Y1"), (1, "Y2"), (1, "Y3"), (1, "Y4"), (1, "B")]

    result = reciprocal_rank_fusion([ranking_one, ranking_two], k=1)

    assert result[0] == (1, "A")


def test_large_k_favors_an_item_both_lists_agree_on():
    # Same rankings as above, but a large k flattens the rank differences,
    # so the item both lists agree on (even only at 5th place) wins.
    ranking_one = [(1, "A"), (1, "X1"), (1, "X2"), (1, "X3"), (1, "B")]
    ranking_two = [(1, "Y1"), (1, "Y2"), (1, "Y3"), (1, "Y4"), (1, "B")]

    result = reciprocal_rank_fusion([ranking_one, ranking_two], k=10)

    assert result[0] == (1, "B")
