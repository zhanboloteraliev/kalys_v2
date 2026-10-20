"""Tests for splitting an article into its numbered parts.

Parsing is where silent mistakes live: a splitter that quietly drops a part
loses a rule from the search without anything failing, so these check the
awkward shapes found in the real texts as well as the simple ones.
"""

from kalys.chunk import chunk_text, split_into_parts


def test_splits_numbered_parts():
    text = "1. First rule.\n2. Second rule.\n3. Third rule."
    assert split_into_parts(text) == [
        ("1", "First rule."),
        ("2", "Second rule."),
        ("3", "Third rule."),
    ]


def test_continuation_lines_stay_with_their_part():
    # Article 97 has unnumbered paragraphs that belong to the part above them.
    text = "1. A rule.\n2. Another rule.\nA paragraph that continues part 2.\n3. Third."
    parts = dict(split_into_parts(text))
    assert parts["2"] == "Another rule.\nA paragraph that continues part 2."
    assert parts["3"] == "Third."


def test_article_without_numbered_parts_is_one_chunk():
    text = "Everyone has the right to freedom of association."
    assert split_into_parts(text) == [("", text)]


def test_text_before_part_one_is_kept():
    text = "An introduction.\n1. First rule."
    assert split_into_parts(text) == [("", "An introduction."), ("1", "First rule.")]


def test_sub_items_with_brackets_do_not_start_a_part():
    # Sub-items are written "1)", not "1.", and must stay inside their part.
    text = "1. The court may:\n1) do one thing;\n2) do another.\n2. A second rule."
    parts = dict(split_into_parts(text))
    assert parts["1"] == "The court may:\n1) do one thing;\n2) do another."
    assert parts["2"] == "A second rule."


def test_a_number_out_of_sequence_does_not_start_a_part():
    # "12. " here is a quoted amount, not part 12: the next part would be 2.
    text = "1. A rule about sums.\n12. 5 som is the limit.\n2. The next rule."
    parts = dict(split_into_parts(text))
    assert parts["1"] == "A rule about sums.\n12. 5 som is the limit."
    assert parts["2"] == "The next rule."


def test_blank_article_gives_one_empty_chunk():
    assert split_into_parts("") == [("", "")]


def test_nothing_is_lost_when_splitting():
    text = "1. One.\nstill one.\n2. Two.\n3. Three.\nstill three."
    joined = " ".join(body for _, body in split_into_parts(text))
    for sentence in ["One.", "still one.", "Two.", "Three.", "still three."]:
        assert sentence in joined


def test_chunk_text_carries_the_title_and_part():
    assert chunk_text("Detention", "5", "Before 45 hours.") == (
        "Detention (part 5)\n\nBefore 45 hours."
    )


def test_chunk_text_without_a_part_number():
    assert chunk_text("Detention", "", "A rule.") == "Detention\n\nA rule."


def test_chunk_text_without_a_title():
    assert chunk_text("", "", "A rule.") == "A rule."
