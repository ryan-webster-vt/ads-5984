-- Migration 2 (Phase 4.5): SQLite-backed search-response cache.
--
-- ADR-006 Decision 2: SQLite answers "have I already seen this exact query,
-- and what came back?" while ChromaDB answers "which stored passages are
-- semantically relevant?". Search responses are keyed deterministically so a
-- repeated or gap-driven search never spends another Tavily credit.

CREATE TABLE IF NOT EXISTS search_queries (
    query_id TEXT PRIMARY KEY,
    query_hash TEXT NOT NULL UNIQUE,
    normalized_query TEXT NOT NULL,
    provider TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    game_id TEXT,
    season TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    expires_at TEXT,
    result_count INTEGER NOT NULL DEFAULT 0,
    credits_used REAL NOT NULL DEFAULT 0,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_search_queries_game ON search_queries (game_id);
CREATE INDEX IF NOT EXISTS idx_search_queries_expires ON search_queries (expires_at);

CREATE TABLE IF NOT EXISTS search_hits (
    hit_id TEXT PRIMARY KEY,
    query_hash TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    title TEXT,
    score REAL,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT,
    content TEXT,
    metadata TEXT,
    FOREIGN KEY (query_hash) REFERENCES search_queries (query_hash) ON DELETE CASCADE,
    UNIQUE (query_hash, canonical_url)
);
CREATE INDEX IF NOT EXISTS idx_search_hits_query ON search_hits (query_hash);
CREATE INDEX IF NOT EXISTS idx_search_hits_url ON search_hits (url);
CREATE INDEX IF NOT EXISTS idx_search_hits_published ON search_hits (published_at);
