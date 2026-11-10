PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    title TEXT NOT NULL DEFAULT 'New chat',
    -- Soft-delete tombstone: NULL = live. Set when a session WITH content is
    -- deleted, so Undo can restore it and other devices hide it on next list.
    -- Empty sessions are hard-deleted instead; tombstoned rows are purged after
    -- a retention window (see purge_deleted_sessions).
    deleted_at TEXT,
    -- Fork lineage (SRS v2.30): the session this one was forked FROM, or NULL
    -- for a normal (non-fork) session. ON DELETE SET NULL so purging the parent
    -- orphans the fork gracefully (it falls back to a top-level row) rather than
    -- cascading. The sidebar groups forks under their parent from this column.
    forked_from_session_id INTEGER REFERENCES chat_sessions(id) ON DELETE SET NULL,
    -- Plan H2: the run (answer) this fork branched at; pairs fork siblings for DPO.
    forked_from_run_id INTEGER REFERENCES runs(id) ON DELETE SET NULL,
    -- Plan H3: 1 = the hidden ideation tracing session (never listed/purged).
    internal INTEGER NOT NULL DEFAULT 0,
    -- Plan H4: 1 = agent-mode confirm-tier tools run without asking.
    auto_approve INTEGER NOT NULL DEFAULT 0
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
    is_private INTEGER NOT NULL DEFAULT 0 CHECK (is_private IN (0, 1)),
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

-- Plan H4: external-content full-text index over chunks.text (the agent's
-- search_in_papers). Kept in sync by the triggers; backfilled by apply_schema.
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id'
);
CREATE TRIGGER IF NOT EXISTS chunks_fts_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_fts_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES ('delete', old.id, old.text);
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;

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
    -- F4.5: which deck version snapshot this run stamped (or NULL for
    -- non-slide runs). Used so per-turn DeckChip cards in the chat
    -- replay refer to the version produced by THAT turn — not just the
    -- currently-active one.
    deck_version_id TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running', 'ok', 'error', 'cancelled', 'interrupted')),
    -- Plan H4: 'pipeline' (router + subgraphs) or 'agent' (tool-calling loop).
    mode TEXT NOT NULL DEFAULT 'pipeline',
    -- Plan H4: the agent turn's work record
    -- {"tools": [...], "read": [chunk ids], "cited": [...], "papers": [...], "steps": n}
    agent_record_json TEXT
);

-- Plan H4: a confirm-tier tool call awaiting the user's approval.
CREATE TABLE IF NOT EXISTS pending_actions (
    id TEXT PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL,
    tool TEXT NOT NULL,
    args_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'done', 'dismissed', 'failed')),
    result_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pending_actions_run ON pending_actions(run_id);

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
    -- F4.5: JSON blob (e.g. ``{"kind": "slide_style_global"}``) so the
    -- style_resolver can find the "remembered global slide style" row.
    metadata        TEXT NULL,
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
    -- v2.25 / F4.5: `theme` column dropped (preamble is source of truth via
    -- slide_style_overrides + slide_style_global memory + slide_style_default.tex).
    current_version_id TEXT,
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
    source_sections_json TEXT NOT NULL DEFAULT '[]',  -- per-slide source grounding:
                                             -- JSON [{paper_id, section_name, chunk_ids}],
                                             -- resolved from the frame's % cite: marker
                                             -- (north-star traceback; not shown on slide)
    UNIQUE (deck_id, slide_index)
);

-- v2.25 / F4.5: per-session Beamer preamble override (FR-12 configurable style).
-- Source-of-truth precedence: this table > slide_style_global memory > default file.
CREATE TABLE IF NOT EXISTS slide_style_overrides (
    session_id INTEGER PRIMARY KEY REFERENCES chat_sessions(id) ON DELETE CASCADE,
    preamble_tex TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('user_request','agent_inferred','global_memory_projection')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Runtime configuration overlay (Plan G / FR-14). Durable source of truth
-- for editable .env-class config; projected onto os.environ at boot. A row
-- exists ONLY for keys the user overrode in the Settings panel; absence means
-- "fall back to backend/.env / built-in default".
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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

-- Plan H1: bilingual-reading translation cache. One row per
-- (paper, block key, language); no user column (single user).
CREATE TABLE IF NOT EXISTS translations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_content_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
    block_key TEXT NOT NULL, language TEXT NOT NULL,
    source_hash TEXT NOT NULL, memory_hash TEXT NOT NULL,
    text TEXT NOT NULL, model TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (paper_content_id, block_key, language));

-- Plan H2: feedback flywheel. One row per explicit rating (<=1 per run, enforced
-- by the partial unique index) plus any number of implicit (reviewer) rows.
-- No user column (single user).
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    message_id INTEGER REFERENCES messages(id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('explicit','implicit')),
    polarity TEXT NOT NULL CHECK (polarity IN ('up','down')),
    reasons_json TEXT NOT NULL DEFAULT '[]',
    other_text TEXT,
    correction TEXT,
    trigger TEXT CHECK (trigger IN ('keyword','fork')),
    verdict TEXT CHECK (verdict IN ('confirmed','rejected','uncertain')),
    status TEXT NOT NULL DEFAULT 'final'
        CHECK (status IN ('final','needs_confirmation')),
    reviewer_rationale TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_explicit_run
    ON feedback(run_id) WHERE kind = 'explicit';
CREATE INDEX IF NOT EXISTS idx_feedback_run ON feedback(run_id);
-- One implicit (reviewer) row per answer and trigger. apply_schema dedupes an
-- older DB (keeps the newest row) before this runs.
CREATE UNIQUE INDEX IF NOT EXISTS idx_feedback_implicit_run_trigger
    ON feedback(run_id, trigger) WHERE kind = 'implicit';

CREATE TABLE IF NOT EXISTS feedback_presets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL UNIQUE,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Plan H3: cross-domain ideation. Single user: there is no user column yet.
-- The UNIQUE constraints below (domains.name, the composite primary keys) are
-- deliberate single-user constraints; a future multi-user migration must
-- rebuild those tables with the user column in each key.
CREATE TABLE IF NOT EXISTS mechanism_cards (
    paper_content_id INTEGER PRIMARY KEY
        REFERENCES paper_content(id) ON DELETE CASCADE,
    card_json TEXT NOT NULL,
    digest_hash TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    -- The card prompt slot that built it; a slot or model change rebuilds.
    slot TEXT NOT NULL DEFAULT 'ideation_card/v1',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS domains (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    color_index INTEGER,  -- NULL = gray Other
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- 1 = user-curated (renamed, merge target, or created by a manual edit):
    -- the orphan cleanup in ideation/domains.py never deletes it.
    manual INTEGER NOT NULL DEFAULT 0
);

-- A user-renamed or merged-away domain name (normalized) -> the domain it now
-- belongs to, so re-analysis using the old name keeps the user's edit.
CREATE TABLE IF NOT EXISTS domain_aliases (
    name_norm TEXT PRIMARY KEY,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_domains (
    paper_content_id INTEGER NOT NULL
        REFERENCES paper_content(id) ON DELETE CASCADE,
    domain_id INTEGER NOT NULL REFERENCES domains(id) ON DELETE CASCADE,
    manual INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (paper_content_id, domain_id)
);

-- Library-internal citation edges only (source cites target).
CREATE TABLE IF NOT EXISTS citation_edges (
    source_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
    target_id INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
    PRIMARY KEY (source_id, target_id)
);

CREATE TABLE IF NOT EXISTS citation_external (
    paper_content_id INTEGER NOT NULL
        REFERENCES paper_content(id) ON DELETE CASCADE,
    external_arxiv_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('cites','cited_by')),
    PRIMARY KEY (paper_content_id, external_arxiv_id, direction)
);

CREATE TABLE IF NOT EXISTS citation_fetch (
    paper_content_id INTEGER PRIMARY KEY
        REFERENCES paper_content(id) ON DELETE CASCADE,
    fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
    status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS problems (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    tried TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'generating'
        CHECK (status IN ('generating','ready','error')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    problem_id INTEGER REFERENCES problems(id) ON DELETE CASCADE,
    source_doc INTEGER NOT NULL REFERENCES paper_content(id) ON DELETE CASCADE,
    source_section TEXT NOT NULL,
    source_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
    target_doc INTEGER REFERENCES paper_content(id) ON DELETE CASCADE,
    target_section TEXT,
    target_chunk_ids_json TEXT NOT NULL DEFAULT '[]',
    title TEXT NOT NULL,
    rationale TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new','saved','trying','worked','failed','dismissed')),
    prev_status TEXT
        CHECK (prev_status IS NULL OR prev_status IN
            ('new','saved','trying','worked','failed','dismissed')),
    note TEXT NOT NULL DEFAULT '',
    dismiss_reasons_json TEXT NOT NULL DEFAULT '[]',
    dismiss_note TEXT NOT NULL DEFAULT '',
    ever_saved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ideas_problem ON ideas(problem_id);

CREATE TABLE IF NOT EXISTS idea_sessions (
    idea_id INTEGER NOT NULL REFERENCES ideas(id) ON DELETE CASCADE,
    session_id INTEGER NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    PRIMARY KEY (idea_id, session_id)
);
