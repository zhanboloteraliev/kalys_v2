-- Step 4 schema. One row per act, per edition, per article (one language each).
-- edition_date and the (edition_id, lang, number) pair give an exact citation:
-- act, article, edition date.

-- Step 6: pgvector adds the `vector` column type, for semantic search.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS acts (
    slug TEXT PRIMARY KEY,
    document_code TEXT NOT NULL,
    name_en TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS editions (
    id INTEGER PRIMARY KEY,  -- the Ministry of Justice edition id
    act_slug TEXT NOT NULL REFERENCES acts (slug),
    edition_code INTEGER,
    edition_date DATE NOT NULL,
    label TEXT NOT NULL  -- the raw edition label, e.g. "08.07.2024 №115"
);

CREATE TABLE IF NOT EXISTS articles (
    id SERIAL PRIMARY KEY,
    edition_id INTEGER NOT NULL REFERENCES editions (id),
    lang TEXT NOT NULL CHECK (lang IN ('ru', 'kg')),
    number TEXT NOT NULL,  -- e.g. "71-1" for an added article
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    UNIQUE (edition_id, lang, number)
);

-- Step 6: a keyword-search column for the original text. Postgres has no
-- Kyrgyz dictionary, so kg rows fall back to 'simple' (no stemming) while ru
-- rows use the 'russian' dictionary.
ALTER TABLE articles ADD COLUMN IF NOT EXISTS ts tsvector
    GENERATED ALWAYS AS (
        to_tsvector(CASE WHEN lang = 'ru' THEN 'russian'::regconfig ELSE 'simple'::regconfig END, text)
    ) STORED;
CREATE INDEX IF NOT EXISTS idx_articles_ts ON articles USING GIN (ts);

-- Step 5: a machine translation of one `articles` row (source_lang, number) into
-- English. Kept in its own table, never mixed into `articles`, because it is not
-- an original text (see CLAUDE.md). Always show "Machine translation. Not an
-- official text." next to this text when displaying it - this table does not
-- repeat that label on every row, the display layer adds it.
CREATE TABLE IF NOT EXISTS translations (
    id SERIAL PRIMARY KEY,
    edition_id INTEGER NOT NULL REFERENCES editions (id),
    number TEXT NOT NULL,
    source_lang TEXT NOT NULL DEFAULT 'ru',
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    model TEXT NOT NULL,
    translated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (edition_id, number)
);

-- Step 6: a keyword-search column for the English translation.
ALTER TABLE translations ADD COLUMN IF NOT EXISTS ts tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;
CREATE INDEX IF NOT EXISTS idx_translations_ts ON translations USING GIN (ts);

-- Step 6: a semantic embedding of one article's text in one language (its
-- title + text, from `translations` for 'en' or `articles` for 'ru'/'kg'),
-- for similarity search with pgvector. One embedding per (edition, number,
-- lang): a Kyrgyz query is compared against 'kg' embeddings, not against
-- a cross-lingual guess from the English ones. `model` records which
-- embedding model made the vector, in case we change models later.
CREATE TABLE IF NOT EXISTS embeddings (
    id SERIAL PRIMARY KEY,
    edition_id INTEGER NOT NULL REFERENCES editions (id),
    number TEXT NOT NULL,
    lang TEXT NOT NULL CHECK (lang IN ('en', 'ru', 'kg')),
    embedding vector(768) NOT NULL,
    model TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (edition_id, number, lang)
);
CREATE INDEX IF NOT EXISTS idx_embeddings_vec ON embeddings USING hnsw (embedding vector_cosine_ops);

-- Step 13: one row per numbered part of an article, not one per article. A long
-- article covers several separate rules, and averaging them into one vector
-- buries the rule a question is actually about. `part` is '' for an article
-- with no numbered parts. The old UNIQUE constraint did not include `part`, so
-- it is replaced by a unique index that does.
ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS part TEXT NOT NULL DEFAULT '';
ALTER TABLE embeddings DROP CONSTRAINT IF EXISTS embeddings_edition_id_number_lang_key;
CREATE UNIQUE INDEX IF NOT EXISTS idx_embeddings_unique
    ON embeddings (edition_id, number, lang, part);

-- Step 11: how many questions have been answered on each day. Used to stop
-- the day's Bedrock spending at a fixed ceiling. Kept in PostgreSQL rather
-- than in the process's memory on purpose: a counter in memory resets every
-- time the service restarts, which is exactly when a spending cap must not
-- forget what it has already allowed.
CREATE TABLE IF NOT EXISTS usage (
    day DATE PRIMARY KEY,
    questions INTEGER NOT NULL DEFAULT 0
);
