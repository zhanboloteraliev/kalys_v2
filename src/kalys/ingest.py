"""Step 3: download all editions of our acts, in Russian and Kyrgyz.

Run:  uv run python -m kalys.ingest
Output:
- data/raw/<act>/document.json            the GetDocument answer (metadata + editions list)
- data/raw/<act>/<edition_id>_<lang>.json  the GetEdition answer, saved byte for byte
- data/raw/manifest.json                   one row per downloaded file
- ingest_report.md                         a short summary to check the data

The site blocks cloud servers, so run this on a home computer (see docs/cbd-api.md).
"""

import hashlib
import json
import re
from datetime import UTC, date, datetime
from pathlib import Path

from kalys.acts import ACTS, LANGUAGES
from kalys.minjust_client import BASE_URL, BlockedError, MinjustClient

RAW_DIR = Path("data/raw")
REPORT_FILE = Path("ingest_report.md")

# These letters exist in Kyrgyz but not in Russian.
KYRGYZ_LETTERS = "ңөүҢӨҮ"


def parse_edition_date(label: str) -> date:
    """Turn an edition label like "08.07.2024 №115" into a date."""
    match = re.match(r"\s*(\d{2})\.(\d{2})\.(\d{4})", label)
    if match is None:
        raise ValueError(f"No date in edition label: {label!r}")
    day, month, year = match.groups()
    return date(int(year), int(month), int(day))


def count_kyrgyz_letters(text: str) -> int:
    return sum(text.count(letter) for letter in KYRGYZ_LETTERS)


def edition_text(answer: dict) -> str:
    """The API puts the text in contentRu, even for lang=kg (see docs/cbd-api.md)."""
    return answer.get("contentKg") or answer.get("contentRu") or ""


def download_act(client: MinjustClient, act, raw_dir: Path) -> list[dict]:
    """Download one act. Returns manifest rows."""
    act_dir = raw_dir / act.slug
    act_dir.mkdir(parents=True, exist_ok=True)

    response = client.get("/api/v1/GetDocument", params={"DocumentCode": act.document_code})
    response.raise_for_status()
    (act_dir / "document.json").write_bytes(response.body)

    rows = []
    editions = response.json()["editions"]
    for number, edition in enumerate(editions, start=1):
        # Trust the editions list for id and date, not the GetEdition answer.
        edition_id = edition["id"]
        label = edition["nameRus"]
        for lang in LANGUAGES:
            print(f"{act.slug}: edition {number}/{len(editions)} ({label}), {lang}")
            params = {"editionId": edition_id, "lang": lang}
            answer = client.get("/api/v1/GetEdition", params=params)
            answer.raise_for_status()

            file = act_dir / f"{edition_id}_{lang}.json"
            file.write_bytes(answer.body)  # raw, untouched

            text = edition_text(answer.json())
            rows.append(
                {
                    "act": act.slug,
                    "document_code": act.document_code,
                    "edition_id": edition_id,
                    "edition_code": edition.get("editionCode"),
                    "edition_label": label,
                    "edition_date": parse_edition_date(label).isoformat(),
                    "lang": lang,
                    "file": str(file.relative_to(raw_dir)),
                    "source_url": answer.url,
                    "bytes": len(answer.body),
                    "text_chars": len(text),
                    "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "kyrgyz_letters": count_kyrgyz_letters(text),
                    "downloaded_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
    return rows


def make_report(rows: list[dict]) -> str:
    """Short checks: editions per act, Kyrgyz text is Kyrgyz, editions are different."""
    lines = ["# Ingest report", ""]
    for act in ACTS:
        act_rows = [row for row in rows if row["act"] == act.slug]
        if not act_rows:
            lines.append(f"## {act.slug}: NOT DOWNLOADED")
            continue
        edition_ids = {row["edition_id"] for row in act_rows}
        lines.append(f"## {act.slug}: {len(edition_ids)} editions, {len(act_rows)} files")

        # Check 1: Kyrgyz text should have Kyrgyz letters, Russian text should not.
        for lang in LANGUAGES:
            counts = [row["kyrgyz_letters"] for row in act_rows if row["lang"] == lang]
            low, high = min(counts, default=0), max(counts, default=0)
            lines.append(f"- {lang}: Kyrgyz letters per file: min {low}, max {high}")

        # Check 2: each edition should have a different text.
        for lang in LANGUAGES:
            hashes = [row["text_sha256"] for row in act_rows if row["lang"] == lang]
            unique = len(set(hashes))
            lines.append(f"- {lang}: {unique} different texts out of {len(hashes)} editions")

        empty = [row["file"] for row in act_rows if row["text_chars"] == 0]
        if empty:
            lines.append(f"- EMPTY TEXT: {empty}")
        lines.append("")
    total_mb = sum(row["bytes"] for row in rows) / 1_000_000
    lines.append(f"Total: {len(rows)} files, {total_mb:.0f} MB")
    return "\n".join(lines) + "\n"


def main() -> None:
    client = MinjustClient()
    rows = []
    try:
        for act in ACTS:
            rows += download_act(client, act, RAW_DIR)
    except BlockedError as error:
        print(f"STOPPED: the site blocked us. {error}")
    finally:
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        manifest = json.dumps(rows, ensure_ascii=False, indent=2)
        (RAW_DIR / "manifest.json").write_text(manifest, encoding="utf-8")
        REPORT_FILE.write_text(make_report(rows), encoding="utf-8")
        print(f"\nSaved {len(rows)} files. Report: {REPORT_FILE}. Source: {BASE_URL}")


if __name__ == "__main__":
    main()
