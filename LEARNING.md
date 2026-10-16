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

## Step 6: hybrid search

**What we built**
- Switched the Postgres image to `pgvector/pgvector:pg16` (same Postgres 16, adds the
  `vector` column type) and added it to `db/schema.sql` via `CREATE EXTENSION vector`.
  No data loss: the container was recreated, the data volume was not.
- `embeddings` table: one semantic embedding per current-edition article **per
  language it has text in** (`en` from `translations`, `ru`/`kg` from `articles`),
  made by a local, multilingual model (`sentence-transformers`,
  `paraphrase-multilingual-mpnet-base-v2`) - no API key, no network call after the
  model is downloaded once, no rate limit. 3,295 embeddings total (1,100 en, 1,100 ru,
  1,095 kg - a few articles have no kg original, see Step 4).
- Generated `tsvector` columns (keyword-search indexes) on `articles.text` (using the
  `russian` dictionary for `ru` rows, `simple` for `kg` - Postgres has no Kyrgyz
  dictionary) and on `translations.text` (using `english`).
- `src/kalys/embed.py`: embeds every language's current-edition articles and loads
  them into `embeddings`. `src/kalys/search.py`: for a question, runs keyword search
  and vector search **in all three languages** (6 ranked lists total), merges them
  with Reciprocal Rank Fusion (RRF), and prints the top results with a citation
  (act, article, edition date, title).

**Why**
- Keyword search alone misses a question that uses different words than the law does;
  semantic search alone can rank a loosely related article above an exact match.
  Combining both and merging with RRF gets the benefits of each.
- A local embedding model avoids repeating Step 5's problem (a free-tier API too slow
  for a bulk job) and needs no API key for this step - the paid key stays reserved for
  the automated-translation pipeline planned in Step 5.
- Searching all three languages for every question, instead of guessing the
  question's language first and searching only that one, removes a failure mode
  entirely - see below for the bug that first revealed it.

**What we learned (the real story: two attempts, not one)**
- *Attempt 1*: embed only the English translation, let a multilingual model match a
  Kyrgyz or Russian question to it cross-lingually. Worked well for Russian and
  English (exact matches ranked 1st) but weak for Kyrgyz (the right article found,
  but only 8th, not 1st) - the model is clearly better at Russian/English than at a
  lower-resource language like Kyrgyz.
- *Attempt 2*: embed the Kyrgyz and Russian originals too (not just English), detect
  the question's language from its letters, and search only that language's
  embeddings and keyword index - the fix we planned. It needed a language guess:
  "kg" if the text has one of Kyrgyz's three letters not used in Russian (ң, ө, ү),
  else "ru" if it has any Cyrillic letter, else "en".
- Testing this against real data found a worse bug than the one we were fixing: a
  short Kyrgyz phrase that happens to use none of those three letters (e.g. "Зарыл
  коргонуу", the Kyrgyz words for "necessary defense") got misread as Russian. Before,
  that only cost some match quality (comparing cross-lingually against English
  embeddings regardless of the guess); now that embeddings are split by language, a
  wrong guess sent the *whole* search to the wrong language's embeddings and the
  correct article did not appear in the results at all - confirmed by querying
  `keyword_search`/`vector_search` directly and finding the right article ranked
  highly in `kg`, but the pipeline had picked `ru`.
- Fix: drop the language guess. Search all three languages for every question (6
  ranked lists: keyword + vector, times en/ru/kg) and let RRF merge them. A question's
  real language naturally wins - its matches are strong across the board, while the
  other two languages contribute weak, scattered noise - without ever having to bet
  everything on one guess. Re-tested: the Kyrgyz title query that completely failed
  under attempt 2 now ranks the correct article 1st.
- Swapping a Docker image for one that adds a Postgres extension does not lose data:
  the named volume (`pgdata`) is untouched by `docker compose up -d`, since only the
  container (not the volume) is recreated. Only the image needs to match the same
  Postgres major version.
- A fresh pull of a newer base image can report a "collation version mismatch" warning
  (different glibc version baked into the image). Postgres's own fix -
  `ALTER DATABASE ... REFRESH COLLATION VERSION` - cleared it; this matters because a
  stale collation version can make index lookups silently wrong on sorted text.
- psycopg3 has no built-in pgvector type: the simplest fix (no extra adapter package)
  is to format a Python list of floats as `"[0.1,0.2,...]"` text and cast it with
  `%s::vector` in the SQL, both for storing and for querying.
- Reciprocal Rank Fusion math: with k=60 (used here), an item ranked 2nd in two lists
  will always outscore an item ranked 1st in only one list; only a large gap between
  ranks (e.g. 1st vs. 5th) lets the single-list top item win, and only for a small k.
  Confirmed by hand before trusting it in `reciprocal_rank_fusion`'s tests.

**Interview questions**
- *What is hybrid search, and why not just use embeddings?* Keyword search is exact
  and explainable (it matches the words asked); semantic search finds meaning-based
  matches keyword search misses. Legal text has exact terms that matter (e.g. "necessary
  defense"), so dropping keyword search would lose precision; dropping semantic search
  would lose questions phrased differently from the law's own wording.
- *Why search all three languages instead of detecting the question's language first?*
  A detector that is only "pretty good" turns one wrong guess into total failure once
  each language has its own embeddings to search - we saw this happen on a real
  example. Searching every language and merging the rankings removes that single
  point of failure: a wrong-language result is just noise that loses the RRF merge,
  not a search that returns nothing relevant.
- *What is Reciprocal Rank Fusion, in one sentence?* Give each item a score of
  `1 / (k + its rank)` in every list it appears in, add the scores up, and re-sort -
  so agreement between search methods counts for more than either one's raw score.
- *Why a local embedding model instead of an API (e.g. Bedrock, OpenAI)?* No per-call
  cost, no rate limit, no API key to manage, and Step 5 already showed a free-tier API
  can be too slow for a one-time bulk job of this size. The trade-off is lower quality
  than a top commercial embedding model, and no option to easily swap models without
  re-running the whole embedding job.
- *Why generate the `tsvector` column instead of computing it at query time?* A
  `GENERATED ... STORED` column is computed once, when the row is written, and can be
  indexed (GIN); computing `to_tsvector()` fresh in every query's `WHERE` clause would
  make Postgres recompute it for every row on every search, with no index to use.
- *What would you tell a teammate who wants to add a "smarter" step (language
  detection, query rewriting, etc.) to a pipeline?* Test it against real data before
  trusting it, specifically looking for the failure case where it is *confidently
  wrong* - a classifier that is wrong 10% of the time is fine if being wrong just
  costs some quality, and dangerous if being wrong breaks the result completely.

## Step 7: answering with AWS Bedrock

**What we built**
- An IAM user (`kalysdev`) for programmatic access only - no console login, no root
  credentials used for anything but creating the user itself. Access key stored only
  in `~/.aws/credentials` (outside the repo, file permissions fixed to `600`), used
  via a named profile (`--profile kalys` / `AWS_PROFILE=kalys` in `.env`), never the
  account's `default` profile.
- A least-privilege IAM policy: just `bedrock:InvokeModel`,
  `bedrock:InvokeModelWithResponseStream`, `bedrock:ListFoundationModels`, and
  `bedrock:GetFoundationModel` - no broad managed policy like
  `AmazonBedrockFullAccess`.
- `src/kalys/answer.py`: given a question, runs `hybrid_search`, fetches the English
  translation + citation for each result, builds a prompt, and asks a Claude model
  through Bedrock to answer only from that text, with exact citations, in the
  question's own language. `BedrockClient` wraps `boto3`'s `invoke_model`, with the
  same retry-on-throttling pattern as Step 5's `GeminiTranslator`.
- `uv run python -m kalys.answer "<question>"` - tested for real in all 3 languages.

**Why**
- Grounding the answer in the actual retrieved article text (never "from the
  model's own knowledge") and requiring inline citations matches `CLAUDE.md`'s core
  promise: exact citations, not just a plausible-sounding answer.
- A least-privilege IAM policy (only the specific Bedrock actions needed, not a
  managed full-access policy) is standard cloud-security practice and good interview
  material for a Cloud Support/Data-focused resume.
- Giving the model only the English translation text (not separate ru/kg text per
  article) keeps the prompt-building code simple and uses articles that always have
  a translation (Step 5 translated every current-edition article); the model is
  strong enough to translate that English text into Russian or Kyrgyz itself when
  answering, while still citing the exact article number and date.

**What we learned**
- AWS Bedrock has an extra access gate beyond IAM: per-model **model access**,
  granted separately in the Bedrock console (or now, automatically on first use -
  the standalone "Model access" page was retired mid-project; see below). An
  `AccessDeniedException` saying a model "is not available for this account" means
  this gate, not a broken IAM policy - confirmed by first getting exactly that error
  with full `InvokeModel` permission already granted.
- Newer Claude models on Bedrock require a **cross-region inference profile ID**
  (`us.anthropic.<model-id>`), not the bare model ID, for `InvokeModel` - the bare ID
  gives a confusing `ValidationException` ("on-demand throughput isn't supported").
  Worth knowing before assuming a model-access problem.
- Anthropic models specifically also need a one-time **"use case details" form**
  submitted for the AWS account, separate from IAM and from per-model access. This
  was not obvious from the (now-retired) Model access page, which only showed a
  general notice about it; what actually triggered the form appearing was opening a
  Claude model in the Bedrock **Playground** and sending a real message there. Before
  submitting it, the exact same `InvokeModel` call that had worked minutes earlier
  (verified by re-running it) started failing with a `ResourceNotFoundException`
  pointing at the form - a reminder that "it worked once" does not mean "it will keep
  working" when a cloud provider's account-level state can change between calls.
- The default `max_tokens` we picked (1024, copied from a quick first test) was too
  small for a real answer with several citations in Russian or Kyrgyz - both test
  answers were cut off mid-sentence, including before the required "Machine
  translation. Not an official text." line. Caught by reading the actual output, not
  by any automated check; raised to 4096 (matching Step 5's translation budget) and
  re-tested to confirm a full, clean answer.
- Testing an LLM API wrapper without hitting the real API: fake the SDK's client
  object (here, a fake with an `invoke_model(modelId, body)` method returning a
  dict shaped like Bedrock's real response, including a `.read()`-able `body`) rather
  than mocking our own `BedrockClient` class - this is the same pattern as Step 5's
  `FakeGeminiClient`, and it means the test also exercises our real JSON
  parsing and retry logic, not just that the fake was called.

**Interview questions**
- *Why a least-privilege IAM policy instead of `AmazonBedrockFullAccess`?* The code
  only ever calls `InvokeModel` and (for diagnostics) `ListFoundationModels` - a
  broader policy would grant the ability to do things the project never needs, like
  changing account-wide model access settings, which is both an unnecessary risk and
  harder to justify in a security review.
- *What is RAG, and where does the "grounding" actually happen in this code?* The
  question is first used to retrieve relevant source text (`hybrid_search`), then
  that text - not the question alone - is put into the prompt, with an explicit
  instruction to answer only from it. The citations work because the model is
  shown the real citation string for each article and told to reuse it exactly,
  not asked to recall one from training data.
- *What happens if hybrid search finds nothing relevant?* `answer()` checks for an
  empty context list and returns a fixed message instead of calling the model at all
  - this avoids a confidently-worded but ungrounded answer, and avoids paying for a
  model call that cannot be grounded in anything.
- *How would you debug "AccessDeniedException: model not available" vs. a real IAM
  problem?* Check whether the IAM policy already allows the action attempted (here,
  `InvokeModel`) - if it does, and the error is specifically about the model, it is
  Bedrock's separate per-model access gate, not an IAM misconfiguration. Two
  different error types (`AccessDeniedException` vs. `ValidationException` about
  inference profiles) pointed at two different causes during this debugging.

## Step 8: the chat web UI (Chainlit)

**What we built**
- `src/kalys/web.py`: a Chainlit app wiring `kalys.answer`'s pipeline (hybrid search
  -> article context -> Bedrock) to a chat window. Three clickable sample questions
  (`cl.set_starters`) per `CLAUDE.md`'s "English default, sample questions" rule.
  The embedding model and Bedrock client load once, when the module is first
  imported (Chainlit keeps one process running), not on every message.
- `BedrockClient.ask_stream()` (new, alongside the existing `ask()`): calls
  Bedrock's `invoke_model_with_response_stream` and yields the answer in small
  pieces as the model generates them, so the UI can show it appearing gradually
  (`msg.stream_token()` per piece) instead of waiting for the whole answer and
  showing it all at once.
- A pure-black theme (`public/kalys.css`, loaded via `.chainlit/config.toml`'s
  `custom_css`), overriding Chainlit's default dark gray.
- Strengthened `SYSTEM_PROMPT` so a greeting or small talk ("hi") gets a normal,
  brief reply instead of being forced through the "answer only from the given
  articles" rule and refusing with "the articles found do not cover this."

**Why**
- A Python-native chat framework (Chainlit) instead of hand-building a React
  frontend keeps the whole project in one language/stack - simpler to build,
  simpler to explain in an interview, and its built-in streaming and starter-question
  features map directly onto `CLAUDE.md`'s UI requirements.
- Real token streaming (not a fake "reveal the finished text slowly" animation)
  reuses the same Bedrock call either way, costs nothing extra, and is more
  honest - the UI reflects when the model is actually still generating.
- Fixing the greeting case in the system prompt (not a separate
  greeting-or-not classifier before retrieval) follows the same lesson as Step 6:
  a bolt-on classifier that is wrong sometimes is a new way for the whole
  pipeline to fail; a single model call that already sees the full context can
  judge "is this a greeting" itself, with nothing extra to get wrong.

**What we learned**
- Chainlit's dark mode uses shadcn/ui-style HSL CSS custom properties
  (`--background: <hue> <saturation>% <lightness>%`) set on an `html.dark` class.
  Overriding just `--background` to `0 0% 0%` was not enough: some wrapper
  elements' visible color did not trace back to any CSS rule we could find via
  `document.styleSheets` at all (checked by walking every matching selector by
  hand, including comma-separated selector lists, and checking `::before`/
  `::after` pseudo-elements) - a reminder that not every color on a page comes
  from a rule you can find by reading the stylesheet, especially in an app
  mixing more than one UI library (Tailwind/shadcn and legacy MUI/Emotion
  components, in Chainlit's case). A blunt, direct `background-color: #000
  !important` on `html`, `body`, and `#root` fixed it regardless of the cause.
- A browser-automation screenshot tool can show a visibly different color than
  what every DOM/CSSOM check agrees is actually rendered (`getComputedStyle`,
  matched-rule inspection, pseudo-element checks all said black; the tool's
  screenshot still showed gray). Asking the owner to check the real browser
  directly settled it - automated verification has its own failure modes, and a
  human looking at the actual page is sometimes the only trustworthy check left.
- Chainlit does not auto-reload on code changes unless started with `-w`; a
  plain `chainlit run` needs a manual restart to pick up an edited `.py` file
  (config/CSS changes, served as static files, do not need a restart - but we
  hit a stale-browser-cache scare there too before realizing the server itself
  just needed restarting for the Python-side fix).
- Tried a question for each of the 3 starter buttons against the live pipeline
  before trusting them: "What is necessary defense under Kyrgyz law?" failed
  to retrieve Criminal Code Article 46 in the top 5 results at all, while the
  shorter "What is necessary defense?" ranked it 1st. The extra, redundant
  phrase ("under Kyrgyz law" - everything in this tool already is Kyrgyz law)
  was enough noise to push the right article out of the results. Worth
  remembering for Step 9 (if a public user's question underperforms, a shorter,
  more direct rephrasing is worth trying before assuming retrieval is broken).
- Chainlit's default auto-generated `.chainlit/translations/` directory (UI
  strings in dozens of languages we never touch) does not belong in git -
  excluded it, kept only our own `config.toml` customization.

**Interview questions**
- *Real token streaming vs. a fake "typewriter" animation on a complete string -
  what is the actual difference, and why does it matter here?* Real streaming
  shows the user output as soon as the model produces it, so perceived latency
  drops (first text appears in under a second instead of after the full
  generation finishes); a fake animation still waits for the complete response
  first and only adds a cosmetic delay on top - slower, not faster, and the
  code must explicitly say that is what it is doing, which is a harder thing to
  defend in a review.
- *Why wasn't a separate "is this a greeting" classifier added before the
  "hi" bug was found?* It wasn't needed - the model already receives the full
  question and context in one call, so a prompt instruction letting it handle
  both cases itself avoids a second classification step that could itself be
  wrong, echoing the Step 6 decision to drop a language-detection step for the
  same reason.
- *How do you debug "the CSS says X but the page shows Y"?* Check the computed
  style (not just the source CSS) to rule out another rule beating yours; check
  for pseudo-elements and other overlapping elements it could be painting
  instead of inheritable `background-color`; and when every automated check
  agrees but the visual result still looks wrong, get a human to look at the
  real thing - tooling itself can be the broken link in the chain.
- *Why keep the embedding model and Bedrock client as module-level singletons
  in `web.py` instead of creating them per message?* Loading the local
  sentence-transformers model takes a few seconds; doing that on every chat
  message would make each question slow for no reason, since the model itself
  does not change between messages.



## Step 9: rewriting the system prompt so it answers like a conversation

### What we built
No new code - only `SYSTEM_PROMPT` in `src/kalys/answer.py` (and three new
starter questions in `web.py`). The old prompt refused too easily: it answered
"the articles found do not cover this" even when the search had found articles
that did cover most of the question.

### Why
The complaint was that the chat felt useless to a normal person. Testing showed
two separate failures, both caused by the prompt, not by the search:

- "what to do when cops stopped me" found five good articles about detention and
  used **none** of them. The prompt made the model choose between "a greeting or
  small talk" and "an actual legal question", and this fell into the greeting
  branch.
- "can police search my phone?" opened with a refusal and then listed the
  articles it had decided not to use, even though two of them were on point.

Five things in the old prompt caused this:

1. A **binary classifier was written into the prompt** (greeting vs. legal
   question). Real questions sit between those two.
2. The refusal was handed to the model as a **literal string**, so it reached
   for that exact phrase and returned it word for word.
3. It was written **almost entirely as prohibitions** ("never", "never",
   "instead of guessing") and said nothing about how to be useful.
4. There was **no partial-answer path**, so a mostly-covered question got a
   refusal rather than most of an answer.
5. "Cite after **every statement**" produced a legal brief instead of a reply.

The new prompt drops the classifier, states the grounding rule once as the rule
with no exception, tells the model to begin with what the articles do say, lets
it answer part of a question and name the gap in one sentence, and asks it to
end with a follow-up question.

### What we learned
- **Making a grounded assistant friendlier makes it less accurate, and you have
  to test for that.** Asking for "plain words, the practical point first" is an
  instruction to flatten things, and legal rules are full of conditions. The
  Russian answer took the 48-hour limit that applies only to a person already
  declared wanted and presented it as the general rule; the real general rule in
  Article 97 part 5 is 45 hours. Nothing was invented - the number was in the
  article - but the condition that governs it was dropped, which in legal text
  is the more dangerous mistake, because the answer still looks right. Two extra
  rules fixed it: keep every condition attached to the rule it governs, and
  check the article's own words before stating any number, deadline or age.
- **Instruction-following gets measurably worse in the lowest-resource
  language.** The same prompt that behaved in English and Russian still drifted
  in Kyrgyz: it produced the globally common "48 hours" figure and invented "for
  children under sixteen" where the article just says "child". It also
  translated the "Machine translation. Not an official text." label, which has
  to stay in English. Testing in only one language would have hidden all of
  this.
- **Check a suspected hallucination against the source before calling it one.**
  A 48-hour "charge or release" rule looked invented. It is real - Article 43
  part 2 - and the model had simply attached it to the wrong article. The fix
  for a mis-citation is different from the fix for a made-up fact, so guessing
  which one you have wastes the effort.
- **Giving a model an exact refusal phrase teaches it to refuse.** Removing the
  quoted sentence and saying "use your own words" was enough to turn blank
  refusals into useful replies that say what was found instead.

### Interview questions
- *Your assistant refused to answer questions it had the information for. How
  did you work out whether the bug was in the search or in the prompt?* Run the
  retrieval step on its own first. The search returned the right detention
  articles ranked 1-5, so retrieval was fine and the prompt was discarding them;
  that one check decides which half of a RAG system to debug and takes seconds.
- *What is the risk of telling a RAG system to answer in plain, practical
  language?* Plain language drops qualifiers, and in law the qualifier is often
  the whole rule. The model stated a time limit that only applies to wanted
  persons as if it were the general rule. The grounding rule ("only use the
  given text") does not protect you here, because the wrong number really was in
  the given text - you need a separate instruction to keep each figure with its
  condition.
- *Why not add a classifier to decide whether a question is a greeting or a
  legal question?* That was tried in the prompt and was the direct cause of the
  worst bug: a real question about being stopped by police was classified as
  small talk and all five retrieved articles were thrown away. It is the same
  lesson as dropping language detection in Step 6 - a guessing step added in
  front of a pipeline can fail in a way that breaks everything behind it.
- *How would you test a change to a prompt?* Fix a small set of questions that
  cover the behaviours you care about - a practical question, a partly covered
  question, an out-of-scope question, a greeting, and each supported language -
  and run them before and after. The Kyrgyz failures only appeared because the
  same question was asked in all three languages.

## Step 10: preparing the AWS deployment

### What we built
`docs/deploy.md` (the runbook), `deploy/setup.sh` (bootstraps a fresh Ubuntu
24.04 instance), `deploy/kalys.service` (systemd unit), and `.env.example`.
One `t3.medium` in `us-east-1` runs everything: PostgreSQL in Docker, the
Chainlit app as a service, and Bedrock through an IAM role on the instance.

### Why
The goal was to stop babysitting a local server and be able to test the chat
from anywhere. A single instance running the same Docker Compose database as
the laptop is the smallest step that achieves that, and it can be stopped when
not in use, which is what keeps the cost near zero.

### What we learned
- **An LLM endpoint is a money leak, so "who can reach it" is a cost question,
  not only a security question.** Every question costs about $0.03-0.05 of
  Bedrock. A public port 8000 lets any passer-by or port-scanning bot spend
  that. The app binds to 127.0.0.1 and is reached over an SSH tunnel
  (`ssh -L 8000:localhost:8000`), so nothing is open except SSH. That is
  cheaper and simpler than adding a login and a spend cap just to test.
- **A server should never hold a copy of your access keys.** An IAM role
  attached to the instance hands it short-lived credentials, and boto3 picks
  them up with no code change at all. The instance's policy is also narrower
  than the laptop's: the server only answers questions, so it gets
  `InvokeModel` and `InvokeModelWithResponseStream` and not the two `List`/`Get`
  actions used while exploring.
- **`AWS_PROFILE` in `.env` is a trap when the same repo runs in two places.**
  On the laptop it selects the `kalysdev` profile; on the server that profile
  does not exist, so naming it would break every Bedrock call even though the
  instance role is working perfectly. The server's `.env` therefore sets only
  `AWS_REGION`. This is the kind of bug that looks like "Bedrock is broken".
- **Compose was publishing PostgreSQL on every interface.** `"5432:5432"` binds
  to `0.0.0.0`, which on a public instance means an internet-reachable database
  whose password is `kalys`. Changed to `"127.0.0.1:5432:5432"`. The security
  group would also have blocked it, but relying on one layer for that is a bad
  trade when the fix costs nothing and changes nothing locally.
- **The 403 from Step 2 shapes the whole deployment.** The source site blocks
  cloud IPs, so the server can never download the law itself. The database has
  to be moved up as a dump, which makes the laptop the permanent source of
  truth and the server a replica. The full dump of all 91 editions compresses
  to 40 MB, so there was no need to trim it to the current editions only - worth
  measuring before designing around a size problem that does not exist.

### What actually happened when we ran it (2026-10-13)
The deployment worked, and the two things that went wrong were both about the
environment rather than the design:

- **The instance was launched on Ubuntu 26.04, not 24.04.** 26.04 has no
  `python3.12` apt package and ships Python 3.14, which PyTorch has no stable
  wheels for - and PyTorch is what the local embedding model runs on. The fix
  was better than the original: `setup.sh` now has uv install and pin its own
  Python 3.12, so the script no longer depends on which Ubuntu you picked.
  Relying on the distro for a language runtime is a hidden version coupling.
- **`docker.io` appeared to not exist.** `apt-cache policy docker.io` returned
  `Candidate: (none)` on the fresh instance, which looks like "this package is
  gone from this release". It was an empty package cache - `apt-get update`
  made it resolve to 29.1.3 immediately. Worth checking the boring explanation
  before redesigning around a missing dependency.

The IAM role worked first time: a real question answered through Bedrock from
the instance with no credentials anywhere on the server, which is the part
that would have been most annoying to debug remotely.

### Switching from SSH to SSM Session Manager
Port 22 had been open to the whole internet, because the owner's IP changes
between networks and a single "My IP" rule kept locking them out. Session
Manager removed the problem rather than managing it: the agent on the instance
connects *outward* to AWS, and you are relayed in through that, so the security
group now has **zero inbound rules** and the owner's address is irrelevant.
Port forwarding still works, so the chat is reached at `localhost:8000` exactly
as before.

Two things this cost us, both worth remembering:

- **The policy went onto the user instead of the role.** The agent's own log
  said it plainly - `assumed-role/KalysServerRole ... is not authorized to
  perform: ssm:UpdateInstanceInformation` - but it took three rounds of asking
  the owner to check the console to find it. Every other step had been "paste
  this JSON", while `AmazonSSMManagedInstanceCore` is an AWS-managed policy you
  attach by name, and that difference was never stated.
- **Not having read access made debugging far slower than the fix.** Granting
  `iam:Get*`/`List*`, `ec2:Describe*` and `ssm:Describe*` found the cause in a
  single query. Read access carries none of the escalation risk that write
  access to IAM does, and IAM read never returns secret keys. It should have
  been offered at the start instead of after the owner got frustrated.

The order mattered: SSM was proven working - registration Online, port forward
returning HTTP 200, and an interactive shell - *before* the SSH rule was
deleted. Closing the only way in before testing the new one is how people lock
themselves out of a server.

### The permissions boundary that silently broke Bedrock
Attaching `AmazonSSMManagedInstanceCore` to `KalysServerRole` fixed SSM and
broke Bedrock in the same click, because in the console it was set as a
**permissions boundary** rather than as an attached policy.

A permissions boundary is a ceiling, not a grant. A role's effective
permissions are the *intersection* of its identity policies and its boundary.
`KalysBedrockInvoke` still allowed both Bedrock actions - the JSON was
correct - but the boundary only permitted SSM, so the intersection was SSM
only:

| Action | Policy allows | Boundary allows | Effective |
| --- | --- | --- | --- |
| SSM | yes | yes | works |
| Bedrock | yes | no | denied |

What made this hard to see:

- **The role looked right.** `list-attached-role-policies` showed both
  `KalysBedrockInvoke` and `AmazonSSMManagedInstanceCore`. Nothing was missing.
  The boundary is a separate field that has to be asked for specifically
  (`aws iam get-role --query Role.PermissionsBoundary`).
- **It only broke the web UI, not the CLI.** The earlier test used
  `invoke_model`, while the chat uses `invoke_model_with_response_stream`, and
  the denial arrived for the streaming call alone. That made it look like a
  streaming or WebSocket problem rather than a permissions one.
- **The error message named the cause exactly** - "because no permissions
  boundary allows the action" - and reading it carefully was worth more than
  any guessing. AWS denial messages say *which* kind of policy refused.

The deeper lesson is about testing: the CLI check on the instance passed on
2026-10-13 and the boundary was added afterwards, so a verification that was
genuinely true stopped being true without anything in the app changing. A
permissions change is a deploy, even when no code moves.

### Interview questions
- *Why not put the app on Lambda and make it serverless?* The chat holds a
  WebSocket open for streaming and keeps a 1 GB embedding model in memory.
  Lambda would reload that model on every cold start and does not fit
  long-lived WebSockets well. App Runner with Aurora Serverless v2 is the
  realistic serverless version, but neither scales to zero cost, and both are
  more setup for an app that is still changing daily.
- *How does the application authenticate to Bedrock in production?* An IAM role
  attached to the EC2 instance. boto3's credential chain finds the instance
  metadata credentials automatically, so the same code works on the laptop
  (named profile) and on the server (role) with no branch in the code and no
  key material on the server.
- *Why is the database loaded from a dump instead of being populated by the
  ingest job?* The source site answers 403 to cloud IP addresses (Step 2), so
  ingestion only works from the owner's laptop. That one external constraint
  decides the architecture: the laptop ingests and translates, and the server
  receives a `pg_dump`.
- *What stops someone else from using your deployed app and spending your
  money?* Nothing is listening on a public port - the app binds to localhost
  and is reached through an SSH tunnel, with only port 22 open, to the owner's
  IP. A public deployment would need a login and a Bedrock spend cap first.
