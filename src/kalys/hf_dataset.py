"""Download the Hugging Face dataset endomorphosis/ipfs_kyrgyzstan_laws for a sanity check.

Run:  uv run python -m kalys.hf_dataset
Output: data/hf/*.parquet and hf_report.md (columns, and the rows for our 3 acts).

We only use this dataset to compare article counts with our own data.
Our source of truth is the Ministry of Justice API.
"""

from pathlib import Path

import httpx
import pyarrow.parquet as pq

from kalys.minjust_client import USER_AGENT

DATASET_URL = "https://huggingface.co/datasets/endomorphosis/ipfs_kyrgyzstan_laws/resolve/main"
FILES = ["data/laws.parquet", "data/articles.parquet"]
HF_DIR = Path("data/hf")
REPORT_FILE = Path("hf_report.md")

# Words to find our acts in Russian titles (same idea as the probe).
ACT_KEYWORDS = {
    "constitution": "конституция кыргызской республики",
    "criminal_code": "уголовный кодекс",
    "criminal_procedure_code": "уголовно-процессуальный кодекс",
}


def download(file: str) -> Path:
    target = HF_DIR / Path(file).name
    if target.exists():
        return target  # already downloaded
    HF_DIR.mkdir(parents=True, exist_ok=True)
    url = f"{DATASET_URL}/{file}"
    with httpx.stream(
        "GET", url, headers={"User-Agent": USER_AGENT}, follow_redirects=True, timeout=120
    ) as response:
        response.raise_for_status()
        target.write_bytes(response.read())
    return target


def short(value) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= 150 else text[:150] + "..."


def main() -> None:
    lines = ["# Hugging Face dataset report", ""]
    tables = {}
    for file in FILES:
        path = download(file)
        table = pq.read_table(path).to_pylist()
        tables[path.stem] = table
        lines.append(f"## {path.name}: {len(table)} rows")
        lines.append("First row:")
        for key, value in table[0].items():
            lines.append(f"- `{key}`: {short(value)}")
        lines.append("")

    # Find our acts in laws.parquet by searching all text in each row.
    lines.append("## Our acts in laws.parquet")
    for act, keyword in ACT_KEYWORDS.items():
        matches = [row for row in tables["laws"] if keyword in str(row.values()).lower()]
        lines.append(f"### {act}: {len(matches)} matching rows")
        for row in matches[:3]:
            fields = {key: short(value) for key, value in row.items() if len(str(value)) < 300}
            lines.append(f"- {fields}")
        lines.append("")

    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Report saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
