"""Test the cbd.minjust.gov.kg API and write a short report.

Run:  uv run python scripts/probe_cbd_api.py
Output: probe_report.md (short summary to share) and data/cache/minjust/ (raw answers).

The script is polite: 1 request per second, cache on disk, stops if blocked.
We do not know the real field names yet, so the script prints the structure
of every answer and searches for fields with "code" or "edition" in the name.
"""

import json
from pathlib import Path

from kalys.minjust_client import BlockedError, MinjustClient

REPORT_FILE = Path("probe_report.md")
TYPE_CONSTITUTION = "0010"
TYPE_CODE = "0030"
STATUS_IN_FORCE = "10"

# Words to find our 3 acts in Russian titles.
ACT_KEYWORDS = {
    "constitution": "конституция",
    "criminal_code": "уголовный кодекс",
    "criminal_procedure_code": "уголовно-процессуальный кодекс",
}

report_lines: list[str] = []


def log(line: str = "") -> None:
    print(line)
    report_lines.append(line)


def shape(value, depth: int = 0):
    """Return a short copy of a JSON value: long text is cut, lists keep 1 item."""
    if isinstance(value, dict):
        if depth > 3:
            return "{...}"
        return {key: shape(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        if not value:
            return []
        return [shape(value[0], depth + 1), f"... {len(value)} items in total"]
    if isinstance(value, str) and len(value) > 120:
        return value[:120] + f"... ({len(value)} chars)"
    return value


def show(title: str, response) -> None:
    log(f"## {title}")
    log(f"- URL: {response.url}")
    log(f"- Status: {response.status_code}, content type: {response.content_type}")
    log(f"- Size: {len(response.body)} bytes")
    try:
        data = response.json()
    except ValueError:
        log(f"- Not JSON. First bytes: {response.body[:80]!r}")
        log()
        return
    log("```json")
    log(json.dumps(shape(data), ensure_ascii=False, indent=2))
    log("```")
    log()


def find_items(data) -> list[dict]:
    """Find the list of documents inside an answer, wherever it is."""
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for value in data.values():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                return value
    return []


def find_fields(data, word: str) -> dict:
    """Find all fields whose name contains `word` (any depth). Returns {path: value}."""
    found = {}

    def walk(value, path):
        if isinstance(value, dict):
            for key, item in value.items():
                if word in key.lower() and not isinstance(item, dict | list):
                    found[f"{path}.{key}"] = item
                walk(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index}]")

    walk(data, "$")
    return found


def all_text(item: dict) -> str:
    return " ".join(str(value) for value in item.values()).lower()


def first_value(fields: dict):
    return next(iter(fields.values()), None)


def main() -> None:
    client = MinjustClient()
    log("# cbd.minjust.gov.kg API probe")
    log()

    show("GetMajorDocuments", client.get("/api/v1/GetMajorDocuments"))

    # 1. Search lists of in-force constitutions and codes.
    found_acts = {}
    for type_id in (TYPE_CONSTITUTION, TYPE_CODE):
        response = client.post(
            "/api/v1/GetDocuments",
            params={"pageNumber": 1, "pageSize": 50},
            json_body={"refTypeId": type_id, "refStatusId": STATUS_IN_FORCE, "lang": "ru"},
        )
        show(f"GetDocuments refTypeId={type_id}", response)
        if response.status_code != 200:
            continue
        for item in find_items(response.json()):
            text = all_text(item)
            for act, keyword in ACT_KEYWORDS.items():
                if act not in found_acts and keyword in text:
                    found_acts[act] = item

    log("## Acts found in GetDocuments")
    for act, item in found_acts.items():
        log(f"- {act}: code fields = {find_fields(item, 'code')}")
    missing = set(ACT_KEYWORDS) - set(found_acts)
    if missing:
        log(f"- NOT FOUND: {sorted(missing)}")
    log()

    # 2. For each act, ask for document details, then one edition in both languages.
    for act, item in found_acts.items():
        code = first_value(find_fields(item, "documentcode")) or first_value(
            find_fields(item, "code")
        )
        if code is None:
            log(f"## {act}: no code field, skipping")
            continue
        response = client.get("/api/v1/GetDocument", params={"DocumentCode": code})
        show(f"GetDocument {act} (DocumentCode={code})", response)
        if response.status_code != 200:
            continue

        # Round 1 showed the real shape: "editions": [{"id": ..., "nameRus": "dd.mm.yyyy"}, ...]
        editions = response.json()["editions"]
        log(f"### All editions of {act} ({len(editions)}): id = date")
        log(", ".join(f"{edition['id']} = {edition['nameRus']}" for edition in editions))
        log()

        # Test the first and the last edition, in both languages.
        edition_ids = sorted({editions[0]["id"], editions[-1]["id"]})
        for edition_id in edition_ids:
            for lang in ("ru", "kg"):
                response = client.get(
                    "/api/v1/GetEdition", params={"editionId": edition_id, "lang": lang}
                )
                show(f"GetEdition {act} editionId={edition_id} lang={lang}", response)
                show_html_start(response)

        # The DOCX fallback: test only once, in both languages.
        if act == "constitution":
            for lang in ("ru", "kg"):
                response = client.get(
                    "/api/v1/GetFile", params={"refId": edition_ids[0], "lang": lang}
                )
                log(f"## GetFile refId={edition_ids[0]} lang={lang}")
                log(f"- Status: {response.status_code}, content type: {response.content_type}")
                log(f"- Size: {len(response.body)} bytes, first bytes: {response.body[:4]!r}")
                log("- (DOCX files start with b'PK')")
                log()


def show_html_start(response) -> None:
    """Print the start of every long text field, so we can see the HTML markup."""
    if response.status_code != 200:
        return
    try:
        data = response.json()
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if isinstance(value, str) and len(value) > 500:
            log(f"Start of `{key}` ({len(value)} chars):")
            log("```html")
            log(value[:2500])
            log("```")
            log()


if __name__ == "__main__":
    try:
        main()
    except BlockedError as error:
        log(f"STOPPED: the site blocked us. {error}")
    finally:
        REPORT_FILE.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
        print(f"\nReport saved to {REPORT_FILE}")
