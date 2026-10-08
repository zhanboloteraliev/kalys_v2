from kalys.parse import Article, extract_body_text, match_articles, parse_articles

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
