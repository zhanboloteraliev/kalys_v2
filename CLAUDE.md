# Kalys v2: project rules

## What this is
- A **resume project** (not a startup) for Cloud Support, Data, and SDE internships (summer 2027).
- Legal search for Kyrgyz law. Questions in English, Kyrgyz, or Russian. Answers in the same
  language with exact citations: act, article, part, edition date.
- Recruiters will read the repo and the live site. Everything must be easy to understand in English.

## How to write code
- Keep code **simple and readable**. The owner must explain every line in interviews.
  Prefer plain functions and the standard library over clever abstractions.
- All code, comments, docs, and commit messages in **English**.
- Python 3.12, uv, ruff (lint + format), pytest. CI runs lint and tests on every push.
- Small commits with clear messages.

## How to talk to the owner
- English is the owner's third language. Explain in **short, simple sentences**.
- Give a short plan before each step. Ask before big decisions.
- After each step, add a short entry to `LEARNING.md`: what we built, why, and likely
  interview questions.

## Data rules
- The original Kyrgyz and Russian texts are the **source of truth**. Never change them.
- English is a machine translation, made once, stored, and reused.
- English text must always be labeled: **"Machine translation. Not an official text."**
- Scope for now: 3 acts only: Constitution (2021), Criminal Code (2021),
  Criminal Procedure Code (No. 129, 28 Oct 2021). All editions, both languages.

## Rules for calling cbd.minjust.gov.kg
- Max **1 request per second**.
- Clear User-Agent with contact info:
  `Kalys/0.1 (student project; +https://eraliev.com; ezhanbolot@gmail.com)`.
- Retries with backoff. Cache every response on disk.
- No captcha solving. No WAF bypass. If blocked: **stop and tell the owner**.

## Never
- Never commit secrets, `.env`, or downloaded data (`data/` is git-ignored).
- No DNS work until the owner asks. (Later: Route 53 hosted zone for
  `kalys.eraliev.com`, delegated with NS records from the current DNS provider.)
  Note: the owner has asked for AWS Bedrock (Step 7) and for deploying to EC2
  (Step 10) - this rule now applies to DNS only.
- Never create, change, or delete AWS resources on the owner's behalf. The
  `kalysdev` key is Bedrock-only by design and must stay that way; console
  steps (instances, roles, security groups) are the owner's to run. See
  `docs/deploy.md`.

## AWS setup (for Bedrock, Step 7)
- IAM user `kalysdev`, programmatic access only, least-privilege policy (just
  `bedrock:InvokeModel`, `InvokeModelWithResponseStream`, `ListFoundationModels`,
  `GetFoundationModel`). Credentials in `~/.aws/credentials` (outside the repo,
  mode 600), used via `AWS_PROFILE=kalys` in `.env` - never the account's default
  profile or root credentials.
- Model: `us.anthropic.claude-sonnet-4-5-20250929-v1:0` in `us-east-1` (a
  cross-region inference profile ID, not the bare model ID - see LEARNING.md).
  The newest "Claude 5" family models are not yet granted for this account.

## Later phases (not now)
AWS deploy, rate limiting + cost cap, monitoring, dashboard.
More acts: the other in-force codes (21 codes in total, type `0030`). Add them in `src/kalys/acts.py`.

## Current status (update after each step)
- Step 1 (setup, CI): done.
- Step 2 (API check): done. See `docs/cbd-api.md`. The site blocks cloud servers (403),
  so all downloads run on the owner's laptop.
- Step 3 (ingest): done. First full run on 2026-10-07: 182 files (91 editions x 2
  languages), no errors. Checked `lang=kg` is really Kyrgyz and that every edition has
  a different text. Found that Kyrgyz-only letters are sometimes HTML numeric entities
  (`&#1199;`), inconsistently between editions - Step 4 must `html.unescape()` before
  parsing. See `docs/cbd-api.md` and the Step 3 entry in `LEARNING.md`.
- Step 4 (parse into articles, match ru/kg, PostgreSQL via Docker Compose, parser tests,
  compare article counts with the Hugging Face dataset): done. `python -m kalys.parse`
  writes `parse_report.md`: Constitution article count matches the Hugging Face
  dataset exactly (116); Criminal Code and Criminal Procedure Code are close (within
  1-2%). Found and fixed a real bug: edition ids are not in date order, so sorting
  them to find "the newest edition" picked an old one. Installed Docker (Fedora's
  `moby-engine` + `docker-compose` packages, since `docker-ce` is not in Fedora's
  repos) and ran `docker compose up -d db` + `uv run python -m kalys.load_db`: loaded
  3 acts, 91 editions, 86,527 article rows into Postgres. Checked row counts and
  spot-checked Constitution Article 1 in both languages - correct, matched text.
  See `docs/cbd-api.md` (surprise 5) and the Step 4 entry in `LEARNING.md`.
- Step 5 (English translation of the current edition of all 3 acts, 1,100 articles
  total: Constitution 116, Criminal Code 435, Criminal Procedure Code 549): done.
  Translated directly by the AI assistant as part of normal response generation (no
  paid API call needed for this one-time backlog). `src/kalys/translate.py` (with
  Claude and Gemini translator classes, disk-cached, retry with backoff) is built and
  ready for later automated translation of new articles and amendments once a paid
  API key is added - a deliberate division of labor the owner chose. Translations
  live in a separate `translations` table (never mixed into `articles`, the source of
  truth), loaded via `python -m kalys.import_translations` after verifying an exact
  article-number match against the Russian source for each act (no missing,
  duplicate, or extra articles). See the Step 5 entry in `LEARNING.md`.
- Step 6 (hybrid search: keyword search + semantic search, merged with Reciprocal
  Rank Fusion): done. Switched the Postgres image to `pgvector/pgvector:pg16` (no
  data loss - same volume, same Postgres 16). Added a local, multilingual embedding
  model (`sentence-transformers`, no API key, no rate limit) and generated `tsvector`
  keyword-search columns. `uv run python -m kalys.embed` loaded 3,295 embeddings
  (every current-edition article, in each language it has text in: en/ru/kg).
  `uv run python -m kalys.search "<question>"` searches all three languages for
  every question (no language-detection step - tried one, found it could misread a
  question and break the search for it completely, see `LEARNING.md`) and merges the
  6 ranked lists with RRF. Tested in all 3 languages: exact matches rank 1st for
  English, Russian, and Kyrgyz. See the Step 6 entry in `LEARNING.md`.
- Step 7 (answer a question with AWS Bedrock, grounded in hybrid-search results,
  with exact citations): done. Set up a least-privilege IAM user for Bedrock (see
  "AWS setup" above). `src/kalys/answer.py`: `uv run python -m kalys.answer
  "<question>"` runs hybrid search, builds a prompt from the top results' English
  translations, and asks Claude (via Bedrock) to answer only from that text, with
  inline citations, in the question's own language. Tested for real in all 3
  languages: grounded, correctly cited, full answers (after raising `max_tokens`
  from 1024 to 4096 - the first test answers were cut off mid-sentence). See the
  Step 7 entry in `LEARNING.md` for the Bedrock access debugging (model access vs.
  IAM vs. inference profiles vs. the Anthropic use-case form - 3 different gates).
- Step 8 (web UI: a chat interface with Chainlit, English default, sample
  questions, streamed answers): done. `src/kalys/web.py` wires the Step 7
  pipeline to a chat window; `uv run chainlit run src/kalys/web.py -w` to run
  it. Pure-black theme (`public/kalys.css` + `.chainlit/config.toml`), 3
  clickable starter questions, and real token-by-token streaming (new
  `BedrockClient.ask_stream()`, not a fake animation). Fixed two real bugs
  found by testing, not just reading the code: one starter question's exact
  wording failed to retrieve the obviously-relevant article (fixed by
  shortening it - see `LEARNING.md`), and a plain greeting ("hi") was wrongly
  refused with "the articles found do not cover this" (fixed in the system
  prompt). See the Step 8 entry in `LEARNING.md` for the black-theme
  debugging story.
- Step 9 (rewrite the system prompt so the chat answers like a conversation):
  done. Prompt-only change in `src/kalys/answer.py`, plus three practical
  starter questions in `web.py`. The old prompt refused questions the search
  had already answered: a real question ("what to do when cops stopped me")
  was classified as small talk and all five retrieved articles were discarded.
  Removed the greeting-vs-legal-question split and the hard-coded refusal
  sentence; the model now starts from what the articles do say, answers part of
  a question when that is all it has, and ends with a follow-up question.
  Testing in all 3 languages found that the friendlier wording made it drop the
  conditions attached to legal rules (it gave the 48-hour limit for wanted
  persons as the general rule, which is 45 hours), so the prompt now has
  explicit rules about conditions and about checking numbers, deadlines and
  ages against the article's own words. Kyrgyz followed the instructions worst
  of the three languages. See the Step 9 entry in `LEARNING.md`.
- Step 10 (deploy to AWS): prepared, not yet run. One `t3.medium` EC2
  instance in `us-east-1` runs everything: PostgreSQL in Docker, the Chainlit
  app as a systemd service, and Bedrock through an IAM role attached to the
  instance (never a copy of the access keys). `docs/deploy.md` is the runbook,
  `deploy/setup.sh` bootstraps a fresh Ubuntu 24.04 instance, and
  `deploy/kalys.service` is the systemd unit. Two decisions worth remembering:
  the app binds to 127.0.0.1 and is reached over an SSH tunnel, so no port is
  open to the internet (every question costs Bedrock money, so an open
  endpoint is a way for strangers to spend it); and the database is moved up
  as a 40 MB `pg_dump` rather than re-downloaded, because the source site
  blocks cloud IPs with a 403 (Step 2). Also changed `docker-compose.yml` to
  publish PostgreSQL on `127.0.0.1` instead of all interfaces.
- The web UI still runs locally; the instance itself has not been created
  yet. No DNS work done.
