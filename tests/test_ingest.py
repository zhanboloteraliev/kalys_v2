import json
from datetime import date

import httpx
import pytest

from kalys.acts import Act
from kalys.ingest import count_kyrgyz_letters, download_act, make_report, parse_edition_date
from kalys.minjust_client import MinjustClient


def test_parse_edition_date_plain():
    assert parse_edition_date("28.10.2021") == date(2021, 10, 28)


def test_parse_edition_date_with_number_suffix():
    assert parse_edition_date("08.07.2024 №115") == date(2024, 7, 8)
    assert parse_edition_date("16.02.2024 № 49") == date(2024, 2, 16)


def test_parse_edition_date_rejects_text_without_date():
    with pytest.raises(ValueError):
        parse_edition_date("latest")


def test_count_kyrgyz_letters():
    assert count_kyrgyz_letters("Кыргыз Республикасынын Конституциясы") == 0
    assert count_kyrgyz_letters("Күчүндө") == 3  # ү, ү, ө


def fake_api(request):
    """A tiny copy of the real API, with the real field names."""
    path = request.url.path
    if path.endswith("/GetDocument"):
        editions = [
            {"id": 101, "editionCode": 10, "nameRus": "28.10.2021"},
            {"id": 7, "editionCode": 20, "nameRus": "08.07.2024 №115"},
        ]
        return httpx.Response(200, json={"documentCode": 112309, "editions": editions})
    if path.endswith("/GetEdition"):
        lang = request.url.params["lang"]
        edition_id = request.url.params["editionId"]
        text = "Берене ү ө" if lang == "kg" else "Статья"
        # Like the real API: the text is in contentRu, and the id in the answer is wrong.
        content = f"<p>{text} {edition_id}</p>"
        return httpx.Response(200, json={"id": 7, "contentRu": content, "contentKg": None})
    return httpx.Response(404)


def test_download_act_saves_raw_files_and_manifest_rows(tmp_path):
    http = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(fake_api))
    client = MinjustClient(cache_dir=tmp_path / "cache", http=http, sleep=lambda seconds: None)
    act = Act("criminal_code", "3-38", "Criminal Code")

    rows = download_act(client, act, tmp_path / "raw")

    assert len(rows) == 4  # 2 editions x 2 languages
    first = rows[0]
    assert first["edition_id"] == 101  # from the editions list, not from the answer
    assert first["edition_date"] == "2021-10-28"
    assert first["lang"] == "ru"
    assert rows[2]["edition_label"] == "08.07.2024 №115"

    saved = tmp_path / "raw" / "criminal_code" / "101_kg.json"
    assert json.loads(saved.read_bytes())["contentKg"] is None  # saved untouched
    assert (tmp_path / "raw" / "criminal_code" / "document.json").exists()

    kg_rows = [row for row in rows if row["lang"] == "kg"]
    assert all(row["kyrgyz_letters"] > 0 for row in kg_rows)

    report = make_report(rows)
    assert "criminal_code: 2 editions, 4 files" in report
    assert "ru: 2 different texts out of 2 editions" in report
