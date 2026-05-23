import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    # ── 2. Workspace + storage ──────────────────────────────────────────
    workspace_dir: Path
    db_path: Path
    papers_cache_dir: Path
    chroma_dir: Path
    max_upload_mb: int

    # ── 3. LLM model selection ──────────────────────────────────────────
    # Router intent classifier.
    router_model: str
    # Chitchat agent.
    chitchat_model: str
    # paper_qa finalizer (cross-paper synthesis; streams to user).
    paper_qa_model: str
    # paper_qa per-paper subagent (section navigation + chunk picking).
    paper_qa_subagent_model: str
    # SQL Agent NL2SQL planner + self-repair (small tier).
    sql_agent_model: str
    # SQL Agent answer phrasing (flagship tier).
    sql_answer_model: str

    # ── 4. Local embedding + rerank (hosted in the modelserver process) ─
    embedding_model: str
    reranker_model: str

    # ── 5. Model server ─────────────────────────────────────────────────
    # The sentence-transformers + cross-encoder live in a SEPARATE process
    # so uvicorn --reload on backend code doesn't reset the ~110 MB
    # embedder + ~80 MB reranker weights. Auto-spawned by the backend's
    # lifespan and reused across reload cycles.
    model_server_host: str
    model_server_port: int
    # When True, skip the HTTP client + auto-spawn and load models in the
    # worker process directly. Used by tests and by hosts that can't run
    # an extra process.
    inprocess_models: bool

    # ── 6. Agent tunables ───────────────────────────────────────────────
    # Maximum number of read_section() calls the subagent makes per paper
    # turn before the loop is force-stopped.
    paper_qa_max_section_reads: int

    # Days a soft-deleted chat session is retained before being permanently
    # purged (cascading its messages/runs/papers) at startup.
    session_retention_days: int

    # ── 7. Memory / recall ──────────────────────────────────────────────
    # Inject recalled memories into paper_qa / library_stats prompts (ON by
    # default). Set to "0" to disable.
    memory_recall_enabled: bool
    # Upgrade-path stub: use semantic (embedding-based) recall instead of
    # FTS. NOT implemented yet — always falls back to FTS when False.
    memory_semantic_enabled: bool

    # ── 9. Report Agent (slides) model selection ────────────────────────
    # Deck planner — decomposes the user request into a slide outline.
    report_plan_model: str
    # Section generator — writes one slide frame per planned section.
    report_section_model: str
    # Speaker notes generator — writes per-frame speaker notes.
    report_notes_model: str
    # Reference resolver — small-tier tool used for citation lookup.
    report_resolve_model: str

    # ── 8. Logging ──────────────────────────────────────────────────────
    log_level: str

    # ── Marker PDF extraction service (v2.19) ───────────────────────────
    marker_service_url: str
    inprocess_marker: bool
    # Max pages sent to Marker per /extract call. A whole large PDF in one
    # call can exhaust a small GPU's VRAM → Marker hot-swaps models between
    # stages → very slow. The client splits the PDF into page-batches of this
    # size and concatenates the (absolute-page-numbered) blocks. Default 1:
    # a single DENSE two-column page already produces 200+ OCR text lines that
    # saturate ~6 GB VRAM; batching >1 such page tips into the CUDA
    # shared-memory fallback (minutes → tens of minutes per call). Raise it for
    # bigger GPUs or sparse single-column papers.
    marker_max_pages: int


def load_settings() -> Settings:
    workspace = Path(os.environ.get("PAPERHUB_WORKSPACE", "./workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    return Settings(
        # 2. Workspace + storage.
        workspace_dir=workspace,
        db_path=workspace / "paperhub.db",
        papers_cache_dir=workspace / "papers_cache",
        chroma_dir=workspace / "chroma",
        max_upload_mb=int(os.environ.get("PAPERHUB_MAX_UPLOAD_MB", "30")),

        # 3. LLM model selection.
        router_model=os.environ.get(
            "PAPERHUB_ROUTER_MODEL", "gemini/gemini-3.1-flash-lite",
        ),
        chitchat_model=os.environ.get(
            "PAPERHUB_CHITCHAT_MODEL", "gemini/gemini-3.1-flash-lite",
        ),
        paper_qa_model=os.environ.get(
            "PAPERHUB_PAPER_QA_MODEL", "gemini/gemini-2.5-pro",
        ),
        paper_qa_subagent_model=os.environ.get(
            "PAPERHUB_PAPER_QA_SUBAGENT_MODEL", "gemini/gemini-3.1-flash-lite",
        ),
        sql_agent_model=os.environ.get(
            "PAPERHUB_SQL_AGENT_MODEL", "gemini/gemini-3.1-flash-lite",
        ),
        sql_answer_model=os.environ.get(
            "PAPERHUB_SQL_ANSWER_MODEL", "gemini/gemini-2.5-pro",
        ),

        # 4. Local embedding + rerank.
        embedding_model=os.environ.get(
            "PAPERHUB_EMBEDDING_MODEL", "BAAI/bge-small-en-v1.5",
        ),
        reranker_model=os.environ.get(
            "PAPERHUB_RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2",
        ),

        # 5. Model server.
        model_server_host=os.environ.get(
            "PAPERHUB_MODEL_SERVER_HOST", "127.0.0.1",
        ),
        model_server_port=int(
            os.environ.get("PAPERHUB_MODEL_SERVER_PORT", "8001"),
        ),
        inprocess_models=os.environ.get(
            "PAPERHUB_INPROCESS_MODELS", "0",
        ) not in ("0", "", "false", "False"),

        # 6. Agent tunables.
        paper_qa_max_section_reads=int(
            os.environ.get("PAPERHUB_PAPER_QA_MAX_SECTION_READS", "8"),
        ),
        session_retention_days=int(
            os.environ.get("PAPERHUB_SESSION_RETENTION_DAYS", "30"),
        ),

        # 7. Memory / recall.
        memory_recall_enabled=os.environ.get(
            "PAPERHUB_MEMORY_RECALL", "1",
        ) not in ("0", "", "false", "False"),
        memory_semantic_enabled=os.environ.get(
            "PAPERHUB_MEMORY_SEMANTIC", "0",
        ) not in ("0", "", "false", "False"),

        # 8. Logging.
        log_level=os.environ.get("PAPERHUB_LOG_LEVEL", "INFO"),

        # Marker PDF extraction service (v2.19).
        marker_service_url=os.environ.get("PAPERHUB_MARKER_URL", "http://127.0.0.1:8002"),
        inprocess_marker=os.environ.get("PAPERHUB_INPROCESS_MARKER", "0") == "1",
        marker_max_pages=int(os.environ.get("PAPERHUB_MARKER_MAX_PAGES", "1")),

        # 9. Report Agent (slides) model selection.
        report_plan_model=os.environ.get(
            "PAPERHUB_REPORT_PLAN_MODEL", "gemini/gemini-2.5-pro",
        ),
        report_section_model=os.environ.get(
            "PAPERHUB_REPORT_SECTION_MODEL", "gemini/gemini-2.5-pro",
        ),
        report_notes_model=os.environ.get(
            "PAPERHUB_REPORT_NOTES_MODEL", "gemini/gemini-2.5-pro",
        ),
        report_resolve_model=os.environ.get(
            "PAPERHUB_REPORT_RESOLVE_MODEL", "gemini/gemini-3.1-flash-lite",
        ),
    )
