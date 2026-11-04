-- Supplier catalog, PostgreSQL 15+ (Neon in production, docker compose locally).
-- Idempotent: applied on every start.

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS suppliers (
    id            BIGSERIAL PRIMARY KEY,
    key           TEXT NOT NULL UNIQUE,          -- merge key: domain, or source:external_id when no site
    name          TEXT NOT NULL,
    website       TEXT,
    domain        TEXT,
    city          TEXT,
    region        TEXT,                          -- office region, NOT a delivery area
    country       TEXT,
    address       TEXT,
    description   TEXT,
    categories    TEXT[] NOT NULL DEFAULT '{}',  -- canonical category ids
    rubrics       TEXT[] NOT NULL DEFAULT '{}',  -- original source rubrics
    products      TEXT[] NOT NULL DEFAULT '{}',  -- product names seen in sources
    status        TEXT NOT NULL DEFAULT 'candidate',
    first_seen    TIMESTAMPTZ NOT NULL,
    last_seen     TIMESTAMPTZ NOT NULL,
    enriched_at   TIMESTAMPTZ,                   -- last supplier-site parse
    -- Python-stemmed text (Russian snowball); Postgres 'simple' config keeps query/index identical.
    search_vector TSVECTOR
);
CREATE INDEX IF NOT EXISTS suppliers_region ON suppliers(region);
CREATE INDEX IF NOT EXISTS suppliers_domain ON suppliers(domain);
CREATE INDEX IF NOT EXISTS suppliers_categories ON suppliers USING GIN(categories);
CREATE INDEX IF NOT EXISTS suppliers_search ON suppliers USING GIN(search_vector);
CREATE INDEX IF NOT EXISTS suppliers_name_trgm ON suppliers USING GIN(name gin_trgm_ops);

CREATE TABLE IF NOT EXISTS contacts (
    supplier_id   BIGINT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL,                 -- phone | email
    value         TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    observed_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (supplier_id, kind, value)
);

-- One row per extracted fact with provenance. Unknown = no row (never a fake 0/false).
CREATE TABLE IF NOT EXISTS facts (
    supplier_id    BIGINT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    field          TEXT NOT NULL,                -- moq | price | certificates | delivery | delivery_regions | inn
    value          TEXT NOT NULL,
    evidence       TEXT,                         -- source text fragment
    source_url     TEXT NOT NULL,
    snapshot_hash  TEXT,
    observed_at    TIMESTAMPTZ NOT NULL,
    parser_version TEXT NOT NULL,
    method         TEXT NOT NULL,                -- catalog_field | regex | jsonld
    verification   TEXT NOT NULL DEFAULT 'extracted',
    PRIMARY KEY (supplier_id, field, value)
);

-- Raw source observations: idempotent replay key = source + edition + external_id.
CREATE TABLE IF NOT EXISTS observations (
    source        TEXT NOT NULL,
    edition       TEXT NOT NULL,
    external_id   TEXT NOT NULL,
    source_url    TEXT NOT NULL,
    payload_hash  TEXT NOT NULL,
    payload       JSONB NOT NULL,
    supplier_id   BIGINT REFERENCES suppliers(id) ON DELETE SET NULL,
    observed_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source, edition, external_id)
);

CREATE TABLE IF NOT EXISTS sources (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    entry_url     TEXT NOT NULL,
    edition       TEXT NOT NULL,
    enabled       BOOLEAN NOT NULL,
    terms_note    TEXT,
    checkpoint    JSONB,
    last_success  TIMESTAMPTZ,
    last_error    TEXT
);

-- Background job queue with leases; workers claim with FOR UPDATE SKIP LOCKED.
CREATE TABLE IF NOT EXISTS jobs (
    id            BIGSERIAL PRIMARY KEY,
    kind          TEXT NOT NULL,                 -- crawl_source | enrich_site
    dedup_key     TEXT NOT NULL UNIQUE,
    payload       JSONB NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending', -- pending | running | done | retry_pending | review_required
    attempts      INT NOT NULL DEFAULT 0,
    run_after     TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until   TIMESTAMPTZ,
    last_error    TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS jobs_ready ON jobs(status, run_after);

-- Searches with too few results; feeds discovery prioritisation.
CREATE TABLE IF NOT EXISTS discovery_requests (
    id            BIGSERIAL PRIMARY KEY,
    query         TEXT NOT NULL,
    region        TEXT,
    results       INT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User notes and shortlist status per supplier (single shared workspace in the prototype, no auth).
CREATE TABLE IF NOT EXISTS notes (
    id            BIGSERIAL PRIMARY KEY,
    supplier_id   BIGINT NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    text          TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS notes_supplier ON notes(supplier_id);

CREATE TABLE IF NOT EXISTS shortlist (
    supplier_id   BIGINT PRIMARY KEY REFERENCES suppliers(id) ON DELETE CASCADE,
    status        TEXT NOT NULL,                 -- to_contact | contacted | negotiating | rejected
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Stems present in the index, for typo correction via pg_trgm similarity.
CREATE TABLE IF NOT EXISTS vocabulary (
    term          TEXT PRIMARY KEY,
    freq          INT NOT NULL
);
CREATE INDEX IF NOT EXISTS vocabulary_trgm ON vocabulary USING GIN(term gin_trgm_ops);

-- FNS SME registry companies (food OKVED). Not shown in search: a row becomes a supplier only after its own
-- website is found, verified (INN or name+city on the site) and has contacts.
CREATE TABLE IF NOT EXISTS registry (
    inn           TEXT PRIMARY KEY,
    ogrn          TEXT,
    name          TEXT NOT NULL,
    okved         TEXT NOT NULL,
    rubric        TEXT NOT NULL,
    categories    TEXT[] NOT NULL DEFAULT '{}',
    region        TEXT,
    region_raw    TEXT,
    city          TEXT,
    msp_category  TEXT,                          -- 1 micro | 2 small | 3 medium
    discovery     TEXT NOT NULL DEFAULT 'new',   -- new | queued | found | no_site | no_contacts
    search_token  TEXT,                          -- pending deferred search
    website       TEXT,
    match         TEXT,                          -- inn | name+city
    supplier_id   BIGINT REFERENCES suppliers(id) ON DELETE SET NULL,
    seen_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    checked_at    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS registry_discovery ON registry(discovery, msp_category);
