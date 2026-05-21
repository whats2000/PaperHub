PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    title TEXT NOT NULL DEFAULT 'New chat'
);

CREATE TABLE IF NOT EXISTS paper_content (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_key TEXT UNIQUE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('arxiv', 'pdf_upload', 'latex_upload')),
    arxiv_id TEXT,
    sha256 TEXT,
    title TEXT NOT NULL,
    authors_json TEXT NOT NULL DEFAULT '[]',
    year INTEGER,
    abstract TEXT NOT NULL DEFAULT '',
    sections_json TEXT,
    source_path TEXT NOT NULL,
    source_dir_path TEXT NOT NULL,
    html_path TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK ((arxiv_id IS NOT NULL) <> (sha256 IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS papers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    paper_content_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1,
    added_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id, paper_content_id)
);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_content_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
    section TEXT,
    char_start INTEGER NOT NULL,
    char_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    dom_id TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    routing_decision_json TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'ok', 'error', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS tool_calls (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    branch TEXT NOT NULL DEFAULT '',
    step_index INTEGER NOT NULL,
    parent_step INTEGER,
    agent TEXT NOT NULL,
    tool TEXT NOT NULL,
    model TEXT,
    args_redacted_json TEXT,
    result_summary_json TEXT,
    latency_ms INTEGER NOT NULL,
    token_in INTEGER,
    token_out INTEGER,
    status TEXT NOT NULL CHECK (status IN ('ok', 'error', 'rejected')),
    error TEXT,
    PRIMARY KEY (run_id, branch, step_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS paper_content_fts USING fts5(
    title,
    abstract,
    content='paper_content',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS paper_content_ai_fts
AFTER INSERT ON paper_content BEGIN
    INSERT INTO paper_content_fts(rowid, title, abstract)
    VALUES (new.id, new.title, new.abstract);
END;

CREATE TRIGGER IF NOT EXISTS paper_content_ad_fts
AFTER DELETE ON paper_content BEGIN
    INSERT INTO paper_content_fts(paper_content_fts, rowid, title, abstract)
    VALUES ('delete', old.id, old.title, old.abstract);
END;

CREATE TRIGGER IF NOT EXISTS paper_content_au_fts
AFTER UPDATE ON paper_content BEGIN
    INSERT INTO paper_content_fts(paper_content_fts, rowid, title, abstract)
    VALUES ('delete', old.id, old.title, old.abstract);
    INSERT INTO paper_content_fts(rowid, title, abstract)
    VALUES (new.id, new.title, new.abstract);
END;
