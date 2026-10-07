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
- No DNS or AWS work until the owner asks. (Later: Route 53 hosted zone for
  `kalys.eraliev.com`, delegated with NS records from the current DNS provider.)

## Later phases (not now)
English translation, hybrid search, Bedrock answers, web UI (English default, sample
questions), AWS deploy, rate limiting + cost cap, monitoring, dashboard.
More acts: the other in-force codes (21 codes in total, type `0030`). Add them in `src/kalys/acts.py`.

## Current status (update after each step)
- Step 1 (setup, CI): done.
- Step 2 (API check): done. See `docs/cbd-api.md`. The site blocks cloud servers (403),
  so all downloads run on the owner's laptop.
- Step 3 (ingest): code done (`python -m kalys.ingest`, `python -m kalys.hf_dataset`).
  First full run on 2026-10-07: 182 files (91 editions x 2 languages), no errors.
  To do: read `ingest_report.md` and `hf_report.md`, check that `lang=kg` text is really
  Kyrgyz and that old editions differ from the newest, update `docs/cbd-api.md`,
  add the Step 3 entry to `LEARNING.md`.
- Step 4 (parse into articles, match ru/kg, PostgreSQL via Docker Compose, parser tests
  with small saved samples, compare article counts with the Hugging Face dataset): next.
- Stop after Step 4 and give the owner a summary. No AWS work yet.
