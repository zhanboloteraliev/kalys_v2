# Kalys

**Search Kyrgyz law in English, Kyrgyz, or Russian, and get answers with exact citations.**

Ask a legal question in any of the three languages. Kalys answers in the same language
and cites the exact source: the act, article, part, and edition date.

> Status: early development (data pipeline). Live demo coming later at https://kalys.eraliev.com.

## Why it matters

- **The 2021 legal reform.** Kyrgyzstan adopted a new Constitution and replaced its main
  codes in 2021, including the Criminal Code and the Criminal Procedure Code. Many old guides
  and answers online are now out of date.
- **Two official languages.** Every law exists in Kyrgyz and Russian. Both texts are
  official, so Kalys keeps both.
- **No official English text.** Foreign investors, researchers, and NGOs cannot read the
  originals. Kalys adds an English machine translation of every article, always shown next to
  the original and always labeled *"Machine translation. Not an official text."*

## Scope (first version)

| Act | Year |
| --- | --- |
| Constitution of the Kyrgyz Republic | 2021 |
| Criminal Code | 2021 |
| Criminal Procedure Code (No. 129, 28 Oct 2021) | 2021 |

Every edition of every act is kept, so a citation always points to a dated version.

## Tech stack

- **Python 3.12**, managed with **uv**
- **PostgreSQL** (local, with Docker Compose) for articles and editions
- **pytest** and **ruff**, run in **GitHub Actions** CI
- Planned: AWS (S3, Amazon Bedrock, Route 53), hybrid search (keywords + vectors)

## Data source

Official texts come from the Ministry of Justice legal database
([cbd.minjust.gov.kg](https://cbd.minjust.gov.kg)). The downloader is polite: max 1 request
per second, a clear User-Agent with contact info, and a local cache, so each page is downloaded only once.

## Roadmap

- [x] Project setup and CI
- [x] Check the Ministry of Justice API ([notes](docs/cbd-api.md))
- [ ] Download all editions of the 3 acts in Kyrgyz and Russian
- [ ] Split texts into articles and store them in PostgreSQL
- [ ] English machine translation of every article
- [ ] Hybrid search
- [ ] Answers with citations (Amazon Bedrock)
- [ ] Web UI (English by default, sample questions)
- [ ] AWS deployment, rate limiting, cost cap, monitoring

## Disclaimer

Kalys is a student portfolio project. It is not legal advice.
