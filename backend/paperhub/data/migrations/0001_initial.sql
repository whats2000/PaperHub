-- Migration: initial schema (Phase A)
-- Matches docs/superpowers/specs/2026-05-17-paperhub-implementation-design.md §6.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE projects (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE papers (
    id            TEXT PRIMARY KEY,
    arxiv_id      TEXT UNIQUE,
    doi           TEXT UNIQUE,
    title         TEXT NOT NULL,
    authors_json  TEXT NOT NULL,
    year          INTEGER,
    abstract      TEXT,
    pdf_path      TEXT NOT NULL,
    sha256        TEXT NOT NULL,
    primary_topic TEXT,
    added_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_papers_year_topic ON papers(year, primary_topic);

CREATE TABLE project_papers (
    project_id     TEXT NOT NULL REFERENCES projects(id),
    paper_id       TEXT NOT NULL REFERENCES papers(id),
    reading_status TEXT CHECK(reading_status IN ('unread','skimmed','deep')),
    PRIMARY KEY (project_id, paper_id)
);

CREATE TABLE tags (
    paper_id TEXT NOT NULL REFERENCES papers(id),
    tag      TEXT NOT NULL,
    PRIMARY KEY (paper_id, tag)
);

CREATE TABLE notes (
    id         TEXT PRIMARY KEY,
    paper_id   TEXT NOT NULL REFERENCES papers(id),
    body_md    TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE chunks (
    id         TEXT PRIMARY KEY,
    paper_id   TEXT NOT NULL REFERENCES papers(id),
    section    TEXT,
    page       INTEGER,
    char_start INTEGER,
    char_end   INTEGER,
    text       TEXT NOT NULL
);
CREATE INDEX idx_chunks_paper ON chunks(paper_id);

CREATE TABLE citations (
    src_paper_id TEXT NOT NULL REFERENCES papers(id),
    dst_paper_id TEXT NOT NULL REFERENCES papers(id),
    source       TEXT NOT NULL,
    PRIMARY KEY (src_paper_id, dst_paper_id)
);

CREATE TABLE chat_sessions (
    id         TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id),
    title      TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE messages (
    id         TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES chat_sessions(id),
    role       TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
    content    TEXT NOT NULL,
    run_id     TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE runs (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT REFERENCES chat_sessions(id),
    routing_decision_json TEXT,
    started_at            TIMESTAMP NOT NULL,
    finished_at           TIMESTAMP,
    status                TEXT CHECK(status IN ('running','ok','failed'))
);

CREATE TABLE tool_calls (
    run_id              TEXT NOT NULL REFERENCES runs(id),
    step_index          INTEGER NOT NULL,
    parent_step         INTEGER,
    agent               TEXT NOT NULL,
    tool                TEXT NOT NULL,
    model               TEXT,
    args_redacted_json  TEXT NOT NULL,
    result_summary_json TEXT,
    latency_ms          INTEGER NOT NULL,
    token_in            INTEGER,
    token_out           INTEGER,
    status              TEXT NOT NULL CHECK(status IN ('ok','error','rejected')),
    error               TEXT,
    PRIMARY KEY (run_id, step_index)
);
CREATE INDEX idx_tool_calls_run ON tool_calls(run_id, step_index);
