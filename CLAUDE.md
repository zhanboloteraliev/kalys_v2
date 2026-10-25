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
- DNS: the owner asked for this on 2026-10-24, so it is no longer blocked.
  **Porkbun is only the registrar; the nameservers are Route 53**
  (`ns-124.awsdns-15.com` and three more), so DNS records are edited in the
  Route 53 hosted zone for `eraliev.com`, not at Porkbun - records set at
  Porkbun are ignored. The zone for the whole domain already exists, so the
  earlier plan of a separate hosted zone for `kalys.eraliev.com` delegated with
  NS records is unnecessary: one A record is the whole job, and no second zone
  is needed. `eraliev.com` also serves the owner's portfolio, so touch only the
  `kalys` record. `kalysdev` has no Route 53 permissions, by design, so this is
  the owner's console step and cannot be verified from here except by
  resolving the name.
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
Monitoring, dashboard. HTTPS + the `kalys.eraliev.com` domain, then making the
site public (do not make it public before checking the Step 11 limits are on).
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
- Step 10 (deploy to AWS): done, running. One `t3.medium` EC2
  instance in `us-east-1` runs everything: PostgreSQL in Docker, the Chainlit
  app as a systemd service, and Bedrock through an IAM role attached to the
  instance (never a copy of the access keys). `docs/deploy.md` is the runbook,
  `deploy/setup.sh` bootstraps a fresh Ubuntu instance (24.04 or newer),
  `deploy/kalys.service` is the systemd unit. Two decisions worth remembering:
  the app binds to 127.0.0.1 and is reached by SSM port forwarding, so no port
  is open to the internet (every question costs Bedrock money, so an open
  endpoint is a way for strangers to spend it); and the database is moved up
  as a 40 MB `pg_dump` rather than re-downloaded, because the source site
  blocks cloud IPs with a 403 (Step 2). Also changed `docker-compose.yml` to
  publish PostgreSQL on `127.0.0.1` instead of all interfaces.
  Deployed for real on 2026-10-13: instance `i-0aa39205f5024aa85` in `us-east-1` at
  54.81.140.243, Ubuntu 26.04, role `KalysServerRole` attached. Verified end
  to end on the server - 86,527 articles / 1,100 translations / 3,295
  embeddings restored, and a real question answered through Bedrock using the
  instance role with no credentials on the box. Two surprises: the instance
  was launched on Ubuntu 26.04, which has no `python3.12` apt package and
  ships Python 3.14 (no stable PyTorch wheels), so `setup.sh` now has uv
  install and pin its own 3.12; and `docker.io` looked unavailable until
  `apt-get update` ran on the fresh box. Access is through AWS Systems Manager
  Session Manager, not SSH: the security group (`launch-wizard-3`,
  `sg-02890acd42ec0fbc3`) has **zero inbound rules** and port 22 is closed.
  The SSM agent connects outward to AWS, so the owner's changing IP no longer
  matters and there is no open port to find. `kalys.pem` is kept only as a way
  back in - re-adding an SSH rule in the console would restore it. An SSM
  shell logs in as `ssm-user`, so `sudo su - ubuntu` is needed to reach the
  project directory. Verified on 2026-10-15 that a stop/start comes back
  clean: the public IP changed (now 3.81.23.59, which no longer matters since
  SSM targets the instance id), and PostgreSQL, the data, and the `kalys`
  service all returned on their own.
- Watch for permissions boundaries on `KalysServerRole`. Attaching
  `AmazonSSMManagedInstanceCore` as a *boundary* (rather than as a policy)
  caps the role at SSM-only and silently blocks Bedrock, which shows up as
  AccessDenied on `InvokeModelWithResponseStream` in the chat while the
  non-streaming CLI path still looks fine. Check with
  `aws iam get-role --role-name KalysServerRole --query Role.PermissionsBoundary`.
- Step 11 (cost controls): done. `src/kalys/limits.py` plus a `usage` table
  cap the chat at `KALYS_MAX_QUESTIONS_PER_SESSION` (default 15) and
  `KALYS_MAX_QUESTIONS_PER_DAY` (default 100, about $4/day of Bedrock). The
  daily count is in PostgreSQL, not memory, so a restart cannot reset the
  budget. Verified with the limit set to 0 that the refusal happens before any
  Bedrock call. The CLI is not limited on purpose. See the Step 11 entry in
  `LEARNING.md`.
- Step 12 (evaluation set): done. `eval/questions.jsonl` holds 125 questions
  (65 en, 30 ru, 30 kg) labelled by act and article number, and
  `uv run python -m kalys.evaluate` scores hybrid search with hit@k, recall@10
  and MRR, writing `eval_report.md` (git-ignored, like the other reports). It
  calls no paid API. Baseline: overall hit@10 0.84, MRR 0.629 - but English is
  0.94, Russian 0.93 and **Kyrgyz only 0.53**. Both halves of the search fail
  for Kyrgyz: there is no Kyrgyz stemmer in PostgreSQL (the `simple` config
  matches whole words only, and Kyrgyz is agglutinative), and the embedding
  model encodes Kyrgyz badly. Fixing Kyrgyz retrieval is the next step, and
  these numbers are the baseline. Do not change the embedding model or the
  instance size without re-running this first. See the Step 12 entry in
  `LEARNING.md`.
- Step 12b (translate the question before searching): measured, not yet built.
  `eval/translation_experiment.py` with hand-made translations in
  `eval/ky_query_translations.json` shows that sending an **English**
  translation of a Kyrgyz question to the search lifts Kyrgyz from hit@10 0.53
  / MRR 0.349 to **0.97 / 0.825**. Russian is second (0.93 / 0.750). Fusing the
  Kyrgyz query back in makes it *worse* than the translation alone, because RRF
  assumes every ranking it merges carries signal - so replace the query, do not
  add to it. Building this at runtime needs a small paid translation call per
  question. A question is ~100 tokens in and ~30 out, so even on the model the
  chat already uses this is a low single-digit percentage of the ~$0.04 an
  answer costs, and less on a smaller model - check current Bedrock prices
  before quoting a figure. The owner has not approved that yet. See the Step 12b entry in
  `LEARNING.md`.
- Step 13 (chunking): done. `src/kalys/chunk.py` splits an article at its
  numbered parts; `embed.py` stores a vector for the whole article (`part = ''`)
  **and** one per part. 13,313 embeddings, up from 3,295. Parts alone were a
  wash (hit@10 flat, MRR down) - they fixed specific questions and broke general
  ones - so both are kept. Result: hit@10 0.84 -> **0.86**, hit@5 0.77 -> 0.81,
  Kyrgyz 0.53 -> **0.60**, Russian 0.93 -> **0.97**; hit@1 and hit@3 unchanged.
  A modest win, not the large one predicted. See the Step 13 entry in
  `LEARNING.md`. (The warning that used to be here - never re-embed on the
  instance because it peaks around 2 GB - applied to the local PyTorch model
  and no longer holds. Since Step 14 embedding is an API call, so running
  `python -m kalys.embed` on the box is light and is now the normal way to
  update it.)
- Also added in Step 12: a guard in the system prompt against working out clock
  times and dates. Asked when a court must review a detention begun at 14:00,
  the model gave the correct rule but mis-computed the deadline in both
  directions, once producing a time before the detention. Improved, not solved:
  it still computes despite the instruction.
- Step 14 (new embedding model, Kyrgyz dropped as a search language): done.
  `BedrockEmbedder` (Amazon Titan Text Embeddings V2, 1024 dims) replaces the
  local sentence-transformers model; `embedder_from_env()` picks it, and
  `KALYS_EMBEDDER=local` selects the old one. `LANGUAGES` is now
  `("en", "ru")` - 8,884 embeddings. Result on the 95 en/ru questions: hit@10
  **0.979**, MRR **0.792**; English MRR 0.705 -> **0.801**, Russian 0.756 ->
  0.773. Overall hit@10 reads 0.77 only because the 30 Kyrgyz questions now
  measure nothing until the routing below is built.
  Three things to remember: Cohere models on Bedrock need an AWS Marketplace
  subscription (`aws-marketplace:Subscribe`) that `kalysdev` does not have, and
  the failure is reported as AccessDenied - read the full message; Titan is
  symmetric so there is no `input_type` to get wrong; and
  `check_embedder_matches` runs at startup everywhere and refuses to search
  when the stored vectors came from a different model than the one embedding
  the question. Re-embedding costs about $0.02 and ~20 minutes.
  See the Step 14 entry in `LEARNING.md`.
- Step 15 (Kyrgyz routed through Russian): done. `src/kalys/kyrgyz.py`
  translates a Kyrgyz question into Russian, searches and answers in Russian,
  and translates the answer back. Wired into `answer()` and the chat. A
  question in Latin script never calls the model, so English costs nothing
  extra. Kyrgyz went from hit@10 0.53 to **0.967**, MRR 0.349 to **0.756** -
  all three languages are now near 0.97. Two things to remember: the
  translation step introduces near-synonym errors that silently retrieve the
  wrong article (`кыйноо`/torture became `истязание`/torment, Article 137 vs
  138), which is why the prompt insists on the term the codes use; and those
  prompt rules were written after seeing which eval questions failed, so the
  real number on unseen questions is a little lower than 0.967. Kyrgyz answers
  are not streamed - the answer has to be finished in Russian before it can be
  translated. A Kyrgyz question costs 3 Bedrock calls instead of 1. See the
  Step 15 entry in `LEARNING.md`.
- Step 16 (deploy the new model and the Kyrgyz routing): done and verified on
  2026-10-22. The instance role's `KalysBedrockInvoke` policy was scoped to
  `anthropic.*` only, so the owner added
  `arn:aws:bedrock:*::foundation-model/amazon.titan-embed-text-v2:0` to it -
  without that, embedding on the server fails with AccessDenied, the same shape
  of trap as the Step 10 permissions boundary. No database dump was needed:
  only the `embeddings` table changed, so `python -m kalys.embed` was re-run on
  the box (8,884 Titan vectors, about $0.02, ~20 minutes, mostly row inserts).
  Verified end to end on the server with a real Kyrgyz question - the instance
  role reaches both Titan and Claude, the answer came back in Kyrgyz, cited
  Article 205 **by part** (parts 1-4), kept every figure, and kept the English
  disclaimer on its own line.
  **Cold start measured on 2026-10-24** after a real stop/start: EC2 LaunchTime
  02:57:21, OS booted 02:57:26, `kalys` active 02:57:37 - **16 seconds from
  launch to the app running** (`systemd-analyze`: 1.2s kernel + 10.1s
  userspace). Everything returned unattended. On a fresh boot the box uses
  **668 MB** of 3,831: Chainlit 138 MB, the Docker daemons ~127 MB, PostgreSQL
  `shared_buffers` a fixed 128 MB, and 14 GB of the 28 GB disk. A 2 GB instance
  therefore fits with room to spare.
  **Measured after dropping the local model:** the app starts in **2 seconds**
  (was ~60 with PyTorch) and uses **~30 MB** (was ~1,500 MB); the whole box
  uses 837 MB of 3,831. So cold start is now essentially just EC2 boot (~35s),
  and a 2 GB instance fits: `t3.small` ~$15/month is a stop-resize-start on the
  current instance, `t4g.small` ~$12/month needs relaunching on an ARM image.
  This is what makes the owner's "sleep the instance and show the GIF while it
  wakes" plan workable - it was not, at ~100 seconds.
  One defect seen: in one citation of four the act name came back garbled
  ("Өйрөп Республикасынын" instead of "Кыргыз Республикасынын"). The article
  number, part and edition date were correct in all four - those are what the
  translation prompt protects - so the damage is cosmetic, but the act name is
  not currently protected and should be.
- Step 17 (resize to `t3.small`): done 2026-10-24. The instance is now
  **`t3.small` (2 GB)**, not `t3.medium` - possible only because Step 14 moved
  embedding to an API and took the app from ~1,500 MB to ~30 MB. Verified after
  a real stop/resize/start: Postgres and the `kalys` service both returned
  unattended, launch to app running took **17 seconds** (boot to active 11s),
  and a real question answered correctly with its citations. Memory: **578 MB
  of 1,905 idle, 727 MB used and 1,177 MB free after answering**, app RSS 26 MB,
  **zero out-of-memory kills**. Costs about $15/month if it ever runs all day,
  against about $30 for `t3.medium`; while stopped both cost the same ~$2.40 of
  disk, so the saving only matters once the site is public and awake.
  Concurrency is still unmeasured: the figures above are one user. The load is
  bounded by the Step 11 cap (100 questions a day), and each concurrent
  question costs roughly one PostgreSQL backend at 5-10 MB, so there is wide
  headroom - but run a real concurrent load test before making the site public.
  To go back: stop, Actions -> Instance settings -> Change instance type ->
  `t3.medium`, start. Stay in the `t3` family; `t4g` is ARM and the existing
  disk will not boot.
- Step 18 (public access: HTTPS and a robot gate): server side done
  2026-10-24, waiting on the owner's console steps. `src/kalys/gate.py` puts a
  Cloudflare Turnstile challenge in front of the chat: a small page with the
  widget, a server-side siteverify call, and a short-lived HMAC-signed cookie.
  It is installed on Chainlit's own FastAPI app, so it covers the **websocket**
  as well as the page - gating only the page would let a bot talk straight to
  the socket. It turns itself off unless all four of `TURNSTILE_SITEKEY`,
  `TURNSTILE_SECRET`, `TURNSTILE_HOSTNAMES` and `KALYS_GATE_SECRET` are set, so
  local runs and the SSM tunnel keep working. `KALYS_GATE_SECRET` is already
  generated in `.env` on the instance (mode 600, never committed).
  `deploy/Caddyfile` terminates HTTPS and proxies to 127.0.0.1:8000; Caddy
  2.11.7 is installed and the config validates, but the service is deliberately
  **disabled** until DNS resolves, because Let's Encrypt limits failed
  certificate attempts per hostname and attempts before DNS exists cannot
  succeed. Enable it with `systemctl enable --now caddy` once the A record is
  live.
  **Live since 2026-10-25 at https://kalys.eraliev.com.** Elastic IP
  **184.194.188.123** is associated with the instance (it costs ~$3.65/month
  whether or not the instance runs - release it if the site is retired). The
  `A` record for host `kalys` is in the Route 53 zone for `eraliev.com`. Ports
  80 and 443 are open in `sg-02890acd42ec0fbc3`; 8000 is not, and must stay
  closed so the gate cannot be bypassed. Caddy holds a Let's Encrypt
  certificate valid to 2027-01-23 and renews it by itself. All four gate keys
  are in `.env` on the instance and the log line reads
  `Turnstile gate ON for kalys.eraliev.com`.
  Verified from outside: the page and the websocket both return 401 without a
  pass and a forged cookie is refused; a real browser solved the challenge
  automatically, reached the chat, and a starter question streamed a correctly
  cited answer through the WebSocket behind Caddy. See the Step 18 entry in
  `LEARNING.md`.
- `kalysdev` was never widened: it is still Bedrock-only, and the instance,
  role, and security group were all created by the owner in the console.
- No DNS work done.
