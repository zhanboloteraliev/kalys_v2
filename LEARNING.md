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
