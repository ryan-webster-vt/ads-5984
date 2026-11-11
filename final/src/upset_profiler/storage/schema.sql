-- SQLite schema for the Upset Condition Profiler.
-- All timestamps are ISO-8601 UTC strings. Structured model payloads are stored
-- as canonical JSON alongside normalized keys used for lookup and indexing.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS games (
    game_id TEXT PRIMARY KEY,
    record_type TEXT NOT NULL CHECK (record_type IN ('historical', 'daily')),
    season TEXT,
    slate_date TEXT,
    game_date TEXT,
    tipoff_at TEXT NOT NULL,
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    completed INTEGER NOT NULL DEFAULT 0,
    winner_team_id TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_season ON games (season);
CREATE INDEX IF NOT EXISTS idx_games_slate_date ON games (slate_date);
CREATE INDEX IF NOT EXISTS idx_games_game_date ON games (game_date);

CREATE TABLE IF NOT EXISTS source_documents (
    document_id TEXT PRIMARY KEY,
    game_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_domain TEXT NOT NULL,
    published_at TEXT,
    retrieved_at TEXT NOT NULL,
    content_hash TEXT,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (game_id) REFERENCES games (game_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_source_documents_game ON source_documents (game_id);
CREATE INDEX IF NOT EXISTS idx_source_documents_published ON source_documents (published_at);

CREATE TABLE IF NOT EXISTS research_runs (
    run_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    summary TEXT,
    payload TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_research_runs_status ON research_runs (status);

CREATE TABLE IF NOT EXISTS quarantine (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT,
    game_id TEXT,
    stage TEXT NOT NULL,
    errors TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_quarantine_run ON quarantine (run_id);
CREATE INDEX IF NOT EXISTS idx_quarantine_game ON quarantine (game_id);

CREATE TABLE IF NOT EXISTS candidate_preconditions (
    precondition_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    feature TEXT NOT NULL,
    source TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_preconditions_feature
    ON candidate_preconditions (feature);

CREATE TABLE IF NOT EXISTS validated_preconditions (
    precondition_id TEXT PRIMARY KEY,
    validation_version TEXT NOT NULL,
    feature TEXT NOT NULL,
    comparison TEXT NOT NULL,
    threshold TEXT NOT NULL,
    validated_at TEXT NOT NULL,
    citations TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_validated_preconditions_validated_at
    ON validated_preconditions (validated_at);

CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    slate_date TEXT,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    payload TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analysis_runs_slate_date ON analysis_runs (slate_date);

CREATE TABLE IF NOT EXISTS monitor_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    game_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    flagged INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_monitor_results_run ON monitor_results (run_id);

CREATE TABLE IF NOT EXISTS upset_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    game_id TEXT NOT NULL,
    score REAL NOT NULL,
    score_rank INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_upset_scores_run_rank ON upset_scores (run_id, score_rank);

CREATE TABLE IF NOT EXISTS cache_entries (
    cache_key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    metadata TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cache_entries_expires ON cache_entries (expires_at);

CREATE TABLE IF NOT EXISTS backtest_predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    game_id TEXT NOT NULL,
    season TEXT NOT NULL,
    predicted_probability REAL NOT NULL,
    actual_upset INTEGER NOT NULL,
    model_name TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (run_id, game_id)
);
CREATE INDEX IF NOT EXISTS idx_backtest_predictions_season
    ON backtest_predictions (season);
