from kalys.embed import article_chunks, vector_literal


def test_vector_literal_formats_as_pgvector_expects():
    assert vector_literal([0.1, -0.2, 1.0]) == "[0.1,-0.2,1.0]"


def test_vector_literal_handles_empty_list():
    assert vector_literal([]) == "[]"


def test_article_chunks_keeps_the_whole_article_and_each_part():
    # '' is the whole article. A general question matches that; a specific one
    # matches a single part. Both are needed, so both are embedded.
    rows = [(1, "97", "Detention", "1. First rule.\n2. Second rule.")]
    chunks = article_chunks(rows)
    assert [(edition, number, part) for edition, number, part, _ in chunks] == [
        (1, "97", ""),
        (1, "97", "1"),
        (1, "97", "2"),
    ]


def test_article_chunks_carries_the_title_into_every_chunk():
    rows = [(1, "97", "Detention", "1. First rule.\n2. Second rule.")]
    for *_, text in article_chunks(rows):
        assert text.startswith("Detention")


def test_the_whole_article_chunk_holds_the_full_text():
    rows = [(1, "97", "Detention", "1. First rule.\n2. Second rule.")]
    whole = next(text for _, _, part, text in article_chunks(rows) if part == "")
    assert "First rule." in whole and "Second rule." in whole


def test_article_without_parts_is_not_embedded_twice():
    # With one part, the part is the article, so there is nothing to add.
    rows = [(1, "36", "Association", "Everyone has the right to freedom of association.")]
    chunks = article_chunks(rows)
    assert len(chunks) == 1
    assert chunks[0][2] == ""
    assert chunks[0][3] == "Association\n\nEveryone has the right to freedom of association."


def test_article_chunks_keeps_articles_separate():
    rows = [
        (1, "97", "Detention", "1. One.\n2. Two."),
        (1, "98", "Search", "1. One."),
    ]
    numbers = [number for _, number, _, _ in article_chunks(rows)]
    assert numbers == ["97", "97", "97", "98"]
