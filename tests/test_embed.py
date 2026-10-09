from kalys.embed import embedding_text, vector_literal


def test_embedding_text_joins_title_and_text():
    assert embedding_text("Guilt", "1. Text.") == "Guilt\n\n1. Text."


def test_embedding_text_with_empty_title_is_just_the_text():
    assert embedding_text("", "1. Text.") == "1. Text."


def test_vector_literal_formats_as_pgvector_expects():
    assert vector_literal([0.1, -0.2, 1.0]) == "[0.1,-0.2,1.0]"


def test_vector_literal_handles_empty_list():
    assert vector_literal([]) == "[]"
