# cbd.minjust.gov.kg API: test results

The Ministry of Justice legal database (cbd.minjust.gov.kg) is a JavaScript app.
Behind it is a JSON API. A public scraper (Sept 2026) described this API.
We tested every endpoint ourselves on **2026-10-05** with `scripts/probe_cbd_api.py`.

Base URL: `https://cbd.minjust.gov.kg/api/v1`

## Summary

| Endpoint | Works? | What it gives |
| --- | --- | --- |
| `GET /GetMajorDocuments` | Yes | 15 main acts (Constitution, big codes) |
| `POST /GetDocuments` | Yes | Search list by type and status, with paging |
| `GET /GetDocument?DocumentCode=` | Yes | Full metadata + **list of all editions with dates** |
| `GET /GetEdition?editionId=&lang=` | Yes, with surprises | Full text of one edition as HTML (Word export) |
| `GET /GetFile?refId=&lang=` | Yes | The same edition as a `.docx` file |

**Access:** the API worked from a home computer. It returned **403 Forbidden** from our
cloud server, even on the home page. So it probably blocks data-center IP addresses.
We did not try to get around this. Downloads run on a local machine.

## Our 3 acts

| Act | `documentCode` (list) | `documentCode` (details) | Editions | First | Last |
| --- | --- | --- | --- | --- | --- |
| Constitution, 5 May 2021 | `1-2` | `112213` | 1 | 05.05.2021 | 05.05.2021 |
| Criminal Code, 28 Oct 2021 No. 127 | `3-38` | `112309` | 49 | 28.10.2021 | 24.07.2026 |
| Criminal Procedure Code, 28 Oct 2021 No. 129 | `3-37` | `112308` | 41 | 28.10.2021 | 28.07.2026 |

Note: the Constitution has only 1 edition in the database (no amendments recorded).

## Endpoints

### GET /GetMajorDocuments
Returns a JSON list (15 items).
```json
[{"documentCode": "1-2", "nameRu": "\"Конституция Кыргызской Республики\" от 5 мая 2021 года ...",
  "nameKg": "2021-жылдын 5-майындагы \"Кыргыз Республикасынын Конституциясы\" ...",
  "status": {"code": "10", "nameRus": "Действует", "nameKyr": "Күчүндө"},
  "lastEdition": 1202952}]
```

### POST /GetDocuments?pageNumber=1&pageSize=50
Body: `{"refTypeId": "0030", "refStatusId": "10", "lang": "ru"}`
```json
{"totalResultsCount": 21, "filteredResultsCount": 21, "recordsTotal": 21, "recordsFiltered": 21,
 "data": [{"documentCode": "3-1", "nameRu": "...", "nameKg": "...", "status": "Действует",
           "dateAdopted": "1996-05-08T00:00:00", "datePublication": "0001-01-01T00:00:00",
           "vid": "Кодекс", "organ": "Жогорку Кенеш Кыргызской Республики",
           "order": 3, "lastEdition": 57538}]}
```
- Verified type codes: `0010` = Constitution (1 result), `0030` = Code (21 results).
  `0020` (law) was **not tested**, because we do not need it now.
- Status `10` = in force ("Действует"). Status `20` = no longer in force ("Утратил силу").
- `datePublication` can be `0001-01-01T00:00:00`, which means "unknown".

### GET /GetDocument?DocumentCode=3-38
Accepts the short code from the list (`3-38`). The answer has a different, numeric
`documentCode` (`112309`). Large: 0.1–1.5 MB, mostly the `documentReferences` list.

Useful fields: `dateAdopted`, `number`, `nameRus`, `nameKyr`, `refTypeId.code`,
`status.code`, and **`editions`**:
```json
"editions": [
  {"id": 1283352, "editionCode": 10, "nameRus": "28.10.2021", "nameKyr": "28.10.2021",
   "textRusType": ".docx", "textKyrType": ".docx"},
  {"id": 1283353, "editionCode": 20, "nameRus": "18.01.2022", ...},
  ...
  {"id": 56638, ..., "nameRus": "24.07.2026", ...}
]
```
- The list is ordered from oldest to newest. `editionCode` goes 10, 20, 30, ...
- `nameRus` is the edition date as `dd.mm.yyyy`. Sometimes it has a suffix, for example
  `08.07.2024 №115` or `16.02.2024 № 49`, when two editions have the same date.
- Edition ids are not in date order (old ones are `128xxxx`, newer ones are small numbers).

### GET /GetEdition?editionId=1283352&lang=ru
```json
{"id": 56638, "nameRus": "24.07.2026", "nameKyr": "24.07.2026",
 "contentRu": "<html><head><meta charset=windows-1251><meta name=Generator content=\"Microsoft Word 15 (filtered)\">...",
 "contentKg": null, "dateOfFutureEntry": null}
```
Size: 0.3 MB (Constitution) to 2.7 MB (Criminal Procedure Code, Kyrgyz).

**Surprises (important):**
1. **The text is always in `contentRu`, also for `lang=kg`.** `contentKg` was always `null`.
   The `lang` parameter does change the text: `lang=kg` gives a different length.
   *Not yet checked:* that the `lang=kg` text is really in Kyrgyz.
2. **`id` and `nameRus` in the answer can be wrong.** For the first edition of the
   Criminal Code (`editionId=1283352`, 28.10.2021) the answer said `"id": 56638,
   "nameRus": "24.07.2026"` (the newest edition). But the text length is different from the
   newest edition, so the text is probably the old one.
   **Rule for our code:** take the edition id and date from `GetDocument.editions`,
   never from the `GetEdition` answer. *Not yet checked:* that the text is really the old edition.
3. The HTML is a Microsoft Word export with a big `<style>` block. The `<meta charset>`
   says `windows-1251` or `unicode`, but the JSON is UTF-8. Ignore the meta tag.

### GET /GetFile?refId=1202952&lang=ru
Returns a `.docx` file (`application/vnd.openxmlformats-officedocument.wordprocessingml.document`,
starts with `PK`). Works for `lang=ru` (135 KB) and `lang=kg` (133 KB).
`refId` is the edition id. This is our fallback if the HTML is hard to parse.

## What is missing or unknown
- No clean "article" structure: we must split the HTML into articles ourselves.
- Not yet checked: Kyrgyz text quality and the old-edition text (see surprises 1 and 2).
- No `Last-Modified` or version field, so we cannot ask "what changed since last time".
  We re-download the editions list (`GetDocument`) and fetch only new edition ids.

## Size estimate for a full download (3 acts, all editions, 2 languages)
About 1 + 49 + 41 = 91 editions × 2 languages = **182 `GetEdition` calls**, plus 3 `GetDocument`
calls. At 1 request per second, that is about 3–4 minutes of requests. The raw JSON is about **300 MB**.
