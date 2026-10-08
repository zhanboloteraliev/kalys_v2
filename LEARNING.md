# Learning log

Short notes after each step: what we built, why, and what an interviewer might ask.

## Step 1: Project setup

**What we built**
- A Python 3.12 project managed with `uv`. `uv.lock` pins the exact version of every package.
- `ruff` checks code style and finds common bugs. `pytest` runs the tests.
- A GitHub Actions workflow (`.github/workflows/ci.yml`) runs lint and tests on every push.
- `.gitignore` keeps `data/` and secrets out of git.
- `README.md` for recruiters, and `CLAUDE.md` with the project rules.

**Why**
- A lock file means the same versions on my laptop, in CI, and later on AWS.
- CI catches mistakes before they reach the main branch.
- Downloaded data is large and can be downloaded again, so it does not belong in git.
  Secrets in git are a security risk, even in old commits.

**Interview questions**
- *Why uv and not pip?* uv is fast, manages the Python version too, and makes a lock file.
- *What does CI do here?* On every push, GitHub starts a clean Linux machine, installs
  the locked packages (`uv sync --locked`), and runs `ruff` and `pytest`. A red check means
  do not merge.
- *What if you commit a secret by mistake?* Rotate it (make a new one) at once. Deleting the
  commit is not enough, because someone may already have a copy.
- *Why `src/` layout?* Tests import the installed package, not random files from the
  folder, so import mistakes show up early.

## Step 2: Checking the Ministry of Justice API

**What we built**
- `src/kalys/minjust_client.py`: a polite HTTP client. It waits 1 second between requests,
  sends a User-Agent with my email, retries server errors with backoff (2, 4, 8, 16 s),
  saves every answer to disk, and stops at once on 403, 429, or a captcha.
- `scripts/probe_cbd_api.py`: calls each endpoint and writes a short report.
- `docs/cbd-api.md`: what really works, real field names, and surprises.

**What we learned**
- The site blocks our cloud server (403), but works from a home computer.
  Many government sites block data-center IPs. We did not try to bypass it.
- The API lists all editions of each act with dates. The Criminal Code has 49 editions.
- The API has bugs: the text is always in `contentRu` (also for Kyrgyz), and the
  `id`/date in the answer can be wrong. So we trust the editions list, not the text answer.

**Interview questions**
- *How do you scrape a site politely?* Rate limit, clear User-Agent with contact info,
  cache so you never download twice, back off on errors, stop when blocked.
- *Why test the API before writing the downloader?* The public notes were not verified.
  Real answers showed 3 surprises. Code built on guesses would store wrong dates.
- *Why cache raw answers?* We can re-run the parser many times without new requests,
  and we keep the original data as proof of where each citation came from.
- *What is exponential backoff?* After each failure, wait twice as long (2, 4, 8, 16 s),
  so a struggling server gets time to recover.

## Step 3: Downloading all editions

**What we built**
- `src/kalys/ingest.py`: downloads every edition of our 3 acts, in Russian and Kyrgyz,
  using the rules from `docs/cbd-api.md` (editions list for id/date, not the text answer).
  Saves raw JSON per edition plus a manifest and `ingest_report.md` with sanity checks.
- `src/kalys/hf_dataset.py`: downloads a public Hugging Face dataset of Kyrgyz laws and
  checks our 3 acts also appear there, as an outside sanity check on our own scrape.
- First full run, 2026-10-07: 182 files (91 editions x 2 languages), no errors, 349 MB.

**What we checked and learned**
- `lang=kg` really is Kyrgyz text (read the HTML body by hand: real Kyrgyz words and
  grammar, not Russian). Confirms the surprise noted in Step 2.
- Every one of the 182 files has a different text hash, so old editions really do differ
  from the newest one (not a bug that serves the same text for every edition id).
- New surprise: Kyrgyz-only letters (ң, ө, ү) are sometimes written as HTML numeric
  entities (`&#1199;`) instead of plain UTF-8 characters, and this is inconsistent between
  editions of the *same* act (older Word exports vs. newer ones). Our simple letter-count
  check in `ingest.py` only looks for literal characters, so it reported "0 Kyrgyz letters"
  for some editions that are actually Kyrgyz. Not a bug in the download, but something the
  Step 4 article parser must handle with `html.unescape()` before it looks at the text.
- In the Hugging Face dataset, searching all fields for a keyword (like "уголовный кодекс")
  also matches unrelated laws whose *body text* happens to mention that code. Matching by
  the exact `identifier` field (`1-2`, `3-38`, `3-37`) is the reliable way to find our acts
  there, for the article-count comparison planned in Step 4.

**Interview questions**
- *How do you know the downloaded Kyrgyz text is really Kyrgyz, not Russian with a
  different label?* Read the raw HTML by hand and check for Kyrgyz-only letters and
  grammar, instead of trusting the API's own labels (the API already showed one bug
  with wrong labels in Step 2).
- *Why hash the text of every edition?* A cheap way to catch "the API returns the same
  content for every edition id" without reading all 182 files by hand.
- *Why check against an outside dataset at all, if cbd.minjust.gov.kg is the source of
  truth?* It is a second, independent source. If our scrape and an unrelated dataset
  disagree sharply (e.g. article counts), that is a sign our parser is wrong, even though
  we never treat the outside dataset as more correct than the ministry's own API.
- *What is `html.unescape()` for?* HTML text can write any character as a numbered code
  (`&#1199;`) instead of the character itself. `unescape()` turns those codes back into
  real text so string checks (like counting Kyrgyz letters) work correctly.

## Step 4: Parsing articles and PostgreSQL

**What we built**
- `src/kalys/parse.py`: turns one edition's Word-HTML export into plain text
  (`extract_body_text`), splits that text into articles by heading pattern
  (`Статья N.` in Russian, `N-берене` in Kyrgyz), and pairs up the ru/kg articles of
  the same edition by article number (`match_articles`). `uv run python -m kalys.parse`
  writes `parse_report.md`: article counts per act, how many article numbers are
  missing in one language, and a comparison with the Hugging Face `article_count`.
- `db/schema.sql` + `docker-compose.yml`: a small Postgres schema (`acts`, `editions`,
  `articles`), one row per article per language per edition.
- `src/kalys/load_db.py`: reads the raw files, parses them, and loads everything into
  Postgres. Safe to run twice (`ON CONFLICT ... DO NOTHING`).
- `tests/test_parse.py`: parser tests with small hand-written HTML snippets (the same
  style as `test_ingest.py`), covering tag stripping, entity unescaping, the
  superscript-to-hyphen fix, and article matching.

**What we learned**
- Legal codes mark an "added article" (inserted between two existing articles after an
  amendment, e.g. a new article between 71 and 72) with a superscript digit:
  "Статья 71¹" means "Article 71-1". The Word export writes that superscript three
  different ways in the same document: a `<sup>1</sup>` tag, a named entity (`&sup1;`),
  or rarely a raw Unicode character (`¹`). All three needed the same fix: turn the
  superscript into a plain `-1` before splitting into articles. Without this fix, two
  unrelated articles can collide under the same number (we found "Статья 211" meaning
  two different things: article 211, and article 21 + a mis-rendered superscript "1").
- Found and fixed a real bug while building the Hugging Face comparison: edition ids
  are **not** in date order (see Step 2/3), so `sorted(edition_ids)` picked an old
  edition and called it "newest". Iterating the manifest in its original (download)
  order fixed it. A good reminder to re-check sort assumptions every time, not just once.
- Our article counts for the current edition are very close to the Hugging Face
  dataset's: Constitution 116 = 116 (exact), Criminal Code 435 ru / 434 kg vs. their
  428, Criminal Procedure Code 549 ru / 545 kg vs. their 547. Close is expected (two
  independent parsers, maybe two different snapshots), exact would be suspicious.
- A few articles appear in `ru` but not `kg` (and vice versa) even in the current
  edition: by hand we found one case where the Kyrgyz heading is simply missing the
  word "берене" (likely a one-off error in the ministry's own document, not our
  parser). We report these counts instead of hiding or guessing around them, since the
  original texts are the source of truth and we never edit them.
- Installed Docker (Fedora ships it as `moby-engine` + `docker-compose`, not the
  `docker-ce`/`docker-compose-plugin` names from Docker's own repo - those are not in
  Fedora's repos). Ran `docker compose up -d db` and `uv run python -m kalys.load_db`
  for real: loaded 3 acts, 91 editions, 86,527 article rows. Checked the row counts
  per act in `psql`, and read back Constitution Article 1 in both languages by hand -
  correct, matched text in both. After `usermod -aG docker`, a brand new group only
  applies in a new login shell; `sg docker -c "..."` runs one command with that group
  right away, without logging out.

**Interview questions**
- *How do you turn messy Word-export HTML into clean text?* `html.unescape()` for
  entities, then a small `HTMLParser` subclass: skip `<style>`/`<script>` content,
  insert a newline at block tags (`<p>`, `<div>`, ...) so paragraphs do not run
  together, and insert a hyphen at `<sup>` tags for the added-article convention.
- *Why not use a ready-made HTML-to-text library?* The standard library's
  `html.parser` is enough for this one, narrow job, and it is easier to explain exactly
  what it does line by line - matching the project rule to keep code simple.
- *How do you know your parser is roughly correct, without a hand-built answer key?*
  Compare article counts against an independent dataset (Hugging Face) built by someone
  else, from the same ministry source. Close numbers are reassuring; wildly different
  numbers would mean a bug.
- *Why `ON CONFLICT DO NOTHING` in `load_db.py` instead of `UPDATE`?* The ministry's
  texts for a past edition never change (we re-download only new editions in Step 3's
  design), so we never need to overwrite an article's text - only add new ones. Simpler
  than deciding whether to update.
- *What would happen if you trusted the first version of this parser?* It would have
  reported the wrong edition as "the current law" for two of the three acts, because of
  the `sorted(edition_ids)` bug. Comparing against the Hugging Face numbers (and reading
  `docs/cbd-api.md` again) is what caught it.

## Step 5: English translation

**What we built**
- A `translations` table in Postgres (separate from `articles`, since a machine
  translation is not an original text): one row per edition per article number, with
  the model/source that produced it.
- `src/kalys/translate.py`: a translator interface with two implementations
  (`ClaudeTranslator`, `GeminiTranslator`), disk-cached per article, that can later
  translate new articles and amendments automatically once a paid API key is in use.
  Includes retry with backoff on rate-limit (429) and server-overload (5xx) errors.
- `src/kalys/export_for_translation.py` / `import_translations.py`: a plain-text,
  numbered-block format (`=== ARTICLE N ===` / `TITLE:` / `TEXT:`) for moving all
  current-edition articles of the 3 acts out of Postgres and back in after translation.
- The actual one-time translation of all 1,100 current-edition articles (Constitution
  116, Criminal Code 435, Criminal Procedure Code 549), done directly by the AI
  assistant as part of normal response generation - no paid API call needed for this
  one-time backlog.

**Why**
- Keeping translations in their own table means the original `articles` rows (the
  source of truth, per `CLAUDE.md`) are never touched, and the English text can always
  be re-generated or corrected without re-parsing anything.
- A pluggable translator interface means the one-time manual backlog and the future
  automated pipeline (for new articles/amendments) share the same prompt, parsing,
  and caching logic - only the "who answers the prompt" part differs.
- Free-tier automated translation (Gemini) was too slow in practice (about one article
  every 5 minutes, 90+ hours projected for ~1,100 articles) to use for a one-time
  backlog, so the backlog was done directly instead, saving the paid API key for later,
  smaller, ongoing translation jobs (new articles, amendments) where speed matters less.

**What we learned**
- `export`'ing an env var in one terminal does not reach a separate shell/tool
  invocation; `python-dotenv` + a `.env` file solves this reliably.
- The installed `google-genai` SDK raises `APIStatusError` subclasses that all expose
  `.status_code`; retry logic must check both 429 (rate limit) and 5xx (server
  overload), not just 429 - the first real API call failed on an uncaught 503.
- A naive prompt can make the model invent a plausible-looking title ("Article 1.")
  for an article that has no title in the original. Explicit negative instructions
  ("do not invent one, return \"\"") are needed, and must be checked against the real
  API, not assumed correct from reading the prompt.
- A batch job must write each result to the database immediately, not only at the very
  end - otherwise an interruption (e.g. stopping a too-slow background run) risks
  losing all progress even if nothing was actually corrupted.
- Verifying a 549-article manual translation by eye is impractical; comparing the
  exact set of article numbers between source and translation (via the same
  `parse_translations()` used for import) catches missing/duplicate/extra articles
  reliably and cheaply before loading into the database.
- A parsing artifact from Step 4 surfaced during translation: the Criminal Code's
  trailing "Приложение 1" (Appendix 1, a list of 28 legal definitions) was never
  recognized as a new heading by the Step 4 parser and got absorbed into the stored
  text of Article 416. The translation was kept faithful to what is actually in the
  database (mercenarism article + the full appendix text), rather than "fixed" outside
  the data pipeline.

**Interview questions**
- *Why a separate `translations` table instead of an `english_text` column on
  `articles`?* The original Kyrgyz/Russian text must never be touched or implied to be
  equivalent to a derived translation; a separate table with its own `model` and
  `translated_at` columns keeps that distinction explicit and queryable.
- *Why build both a Claude and a Gemini translator if only one was used for the
  backlog?* The interface and tests were built for the long-term (paid API, ongoing
  automated translation of amendments); the backlog itself used neither, because
  direct generation was faster and had no incremental cost for a one-time job.
- *How do you verify 1,100 machine-translated legal articles without reading all of
  them?* Structural checks (exact article-number parity, no duplicates/missing) catch
  the most damaging class of error - a dropped or merged article - cheaply. Spot
  checks by hand catch translation-quality issues on a sample.
- *What would you tell a new engineer about trusting output from a free-tier LLM API
  for a time-sensitive batch job?* Measure throughput on a handful of real calls before
  committing to the full job; "it works" and "it works fast enough" are different
  questions.
