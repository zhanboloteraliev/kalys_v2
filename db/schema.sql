-- Step 4 schema. One row per act, per edition, per article (one language each).
-- edition_date and the (edition_id, lang, number) pair give an exact citation:
-- act, article, edition date.

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
