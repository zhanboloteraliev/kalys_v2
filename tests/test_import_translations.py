from kalys.import_translations import parse_translations


def test_parse_translations_single_article():
    reply = """=== ARTICLE 1 ===
TITLE: Principle of Guilt
TEXT:
1. A person is guilty only if proven so.
2. No one is presumed guilty.
"""
    assert parse_translations(reply) == [
        (
            "1",
            "Principle of Guilt",
            "1. A person is guilty only if proven so.\n2. No one is presumed guilty.",
        ),
    ]


def test_parse_translations_multiple_articles():
    reply = """=== ARTICLE 1 ===
TITLE:
TEXT:
1. The Kyrgyz Republic is a sovereign state.

=== ARTICLE 2 ===
TITLE: Principle of Legality
TEXT:
The criminality of an act is determined only by this Code.
"""
    result = parse_translations(reply)
    assert [number for number, _, _ in result] == ["1", "2"]
    assert result[0] == ("1", "", "1. The Kyrgyz Republic is a sovereign state.")
    assert result[1] == (
        "2",
        "Principle of Legality",
        "The criminality of an act is determined only by this Code.",
    )


def test_parse_translations_hyphenated_number():
    reply = """=== ARTICLE 71-1 ===
TITLE: Added Article
TEXT:
Some added text.
"""
    assert parse_translations(reply) == [("71-1", "Added Article", "Some added text.")]


def test_parse_translations_ignores_text_before_first_marker():
    reply = """Here are the translations you asked for:

=== ARTICLE 1 ===
TITLE:
TEXT:
Some text.
"""
    assert parse_translations(reply) == [("1", "", "Some text.")]


def test_parse_translations_empty_reply_gives_no_articles():
    assert parse_translations("") == []
