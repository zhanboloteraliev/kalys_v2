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
