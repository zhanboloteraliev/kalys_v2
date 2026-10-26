from kalys.parse import (
    Article,
    extract_body_text,
    match_articles,
    parse_articles,
    split_continuations,
)

STYLE_BLOCK = "<html><head><style>p{color:red}</style></head><body>"


def test_extract_body_text_strips_tags_and_style():
    content = f"{STYLE_BLOCK}<p>Статья 1</p><p>Текст.</p></body></html>"
    assert extract_body_text(content) == "Статья 1\nТекст."


def test_extract_body_text_unescapes_entities_and_nbsp():
    # &#1199; is "ү", a Kyrgyz-only letter (see docs/cbd-api.md, surprise 4).
    content = f"{STYLE_BLOCK}<p>&#1199;ч&#1199;н&nbsp;бери</p></body></html>"
    assert extract_body_text(content) == "үчүн бери"


def test_extract_body_text_turns_sup_tag_into_hyphen():
    # Статья 71<sup>1</sup> means "Статья 71-1", an added article.
    content = f"{STYLE_BLOCK}<p>Статья 71<sup>1</sup>. Добавленная статья</p></body></html>"
    assert extract_body_text(content) == "Статья 71-1. Добавленная статья"


def test_extract_body_text_turns_sup_entity_into_hyphen():
    content = f"{STYLE_BLOCK}<p>Статья 3&sup1;. Еще одна</p></body></html>"
    assert extract_body_text(content) == "Статья 3-1. Еще одна"


RU_CODE = f"""{STYLE_BLOCK}
<p>Статья 1. Общие положения</p>
<p>1. Текст первой статьи.</p>
<p>2. Вторая часть.</p>
<p>Статья 2<sup>1</sup>. Добавленная статья</p>
<p>Текст добавленной статьи.</p>
</body></html>"""

KG_CODE = f"""{STYLE_BLOCK}
<p>1-берене. Жалпы жоболор</p>
<p>1. Биринчи берененин тексти.</p>
<p>2-1-берене. Кошулган берене</p>
<p>Кошулган берененин тексти.</p>
</body></html>"""

# The Constitution has no article titles: the heading is just the number.
RU_CONSTITUTION = f"""{STYLE_BLOCK}
<p>Статья 1</p>
<p>1. Кыргызская Республика - суверенное государство.</p>
</body></html>"""


def test_parse_articles_ru_with_titles_and_added_article():
    articles = parse_articles(RU_CODE, "ru")
    assert articles == [
        Article("1", "Общие положения", "1. Текст первой статьи.\n2. Вторая часть."),
        Article("2-1", "Добавленная статья", "Текст добавленной статьи."),
    ]


def test_parse_articles_kg_with_hyphenated_number():
    articles = parse_articles(KG_CODE, "kg")
    assert articles == [
        Article("1", "Жалпы жоболор", "1. Биринчи берененин тексти."),
        Article("2-1", "Кошулган берене", "Кошулган берененин тексти."),
    ]


def test_parse_articles_ru_without_title():
    articles = parse_articles(RU_CONSTITUTION, "ru")
    assert articles == [
        Article("1", "", "1. Кыргызская Республика - суверенное государство."),
    ]


def test_match_articles_pairs_by_number():
    ru = [Article("1", "A", "ru text"), Article("2", "B", "ru text 2")]
    kg = [Article("1", "A", "kg text")]  # article 2 is missing in kg

    matched = match_articles(ru, kg)

    assert [m.number for m in matched] == ["1", "2"]
    assert matched[0].ru.text == "ru text" and matched[0].kg.text == "kg text"
    assert matched[1].ru is not None and matched[1].kg is None


def test_match_articles_sorts_added_articles_after_their_base_number():
    ru = [Article("2", "", ""), Article("1-1", "", ""), Article("1", "", "")]

    matched = match_articles(ru, [])

    assert [m.number for m in matched] == ["1", "1-1", "2"]


# --- articles published without the word "Статья" (see CONTINUATION_HEADING) ---


def test_an_added_article_without_a_heading_word_is_split_out():
    # The real case from the Labour Code: "65 -1." with a space in the number
    # and no "Статья", sitting inside the body of article 65.
    body = [
        "1. Всем работникам предоставляются выходные дни.",
        "65 -1. День голосования на выборах",
        "1. День голосования является выходным днем.",
    ]
    articles = split_continuations("65", "Выходные дни", body)
    assert [a.number for a in articles] == ["65", "65-1"]
    assert articles[0].title == "Выходные дни"
    assert articles[1].title == "День голосования на выборах"
    assert "выходные дни" in articles[0].text
    assert "является выходным днем" in articles[1].text


def test_a_normal_article_is_returned_unchanged():
    body = ["1. First part.", "2. Second part."]
    articles = split_continuations("65", "Выходные дни", body)
    assert len(articles) == 1
    assert articles[0].number == "65"
    assert articles[0].text == "1. First part.\n2. Second part."


def test_numbered_parts_are_not_mistaken_for_added_articles():
    # Parts are "1.", "2." and never carry the article's own number.
    body = ["1. First part.", "2. Second part.", "3. Third part."]
    assert len(split_continuations("2", "Заголовок", body)) == 1


def test_a_different_article_number_does_not_split():
    # "67-1." inside article 65 is a cross-reference, not a new article here.
    body = ["1. Смотри 67-1. Что-то еще", "67-1. Другая статья"]
    assert len(split_continuations("65", "Выходные дни", body)) == 1


def test_several_added_articles_in_one_body():
    body = [
        "1. Base text.",
        "65 -1. Первая добавленная",
        "1. Текст первой.",
        "65-2. Вторая добавленная",
        "1. Текст второй.",
    ]
    articles = split_continuations("65", "Выходные дни", body)
    assert [a.number for a in articles] == ["65", "65-1", "65-2"]
    assert articles[2].title == "Вторая добавленная"


def test_an_added_article_keeps_working_for_a_hyphenated_parent():
    # Article "65-1" has base 65, so a "65-2." inside it still splits.
    body = ["1. Text.", "65-2. Ещё одна"]
    assert [a.number for a in split_continuations("65-1", "T", body)] == ["65-1", "65-2"]
