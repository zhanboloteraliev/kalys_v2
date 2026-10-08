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
