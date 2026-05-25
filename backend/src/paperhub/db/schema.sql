PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    title TEXT NOT NULL DEFAULT 'New chat',
    -- Soft-delete tombstone: NULL = live. Set when a session WITH content is
    -- deleted, so Undo can restore it and other devices hide it on next list.
    -- Empty sessions are hard-deleted instead; tombstoned rows are purged after
    -- a retention window (see purge_deleted_sessions).
    deleted_at TEXT
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
    search_results_json TEXT,
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

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL CHECK (scope IN ('session', 'global')),
    session_id  INTEGER REFERENCES chat_sessions(id) ON DELETE CASCADE,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    status      TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'superseded')),
    supersedes      INTEGER NULL REFERENCES memories(id) ON DELETE SET NULL,
    superseded_by   INTEGER NULL REFERENCES memories(id) ON DELETE SET NULL,
    CHECK ((scope = 'global') = (session_id IS NULL))
);

CREATE TABLE IF NOT EXISTS decks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    tex_path TEXT NOT NULL,
    pdf_path TEXT,
    speaker_notes_json TEXT,
    plan_json TEXT,
    page_count INTEGER NOT NULL DEFAULT 0,
    theme TEXT NOT NULL DEFAULT 'metropolis',
    contributing_paper_ids_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'ok' CHECK (status IN ('ok','error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id)
);

CREATE TABLE IF NOT EXISTS deck_slides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id INTEGER NOT NULL REFERENCES decks(id) ON DELETE CASCADE,
    slide_index INTEGER NOT NULL,            -- logical frame order (0-based)
    frame_tex TEXT NOT NULL,                 -- the \begin{frame}…\end{frame} block
    note_text TEXT,                          -- NULL until the NOTES flow runs (opt-in)
    note_language TEXT,                      -- independent of the deck/slide language
    page_start INTEGER NOT NULL,             -- 1-based PDF page this frame starts on
    page_end INTEGER NOT NULL,               -- 1-based PDF page this frame ends on
    UNIQUE (deck_id, slide_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content='memories',
    content_rowid='id',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS memories_ai_fts
AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad_fts
AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS memories_au_fts
AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO memories_fts(rowid, content) VALUES (new.id, new.content);
END;

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
