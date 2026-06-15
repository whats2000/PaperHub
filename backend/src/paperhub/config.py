import os
from dataclasses import dataclass
from pathlib import Path

# F4.4 T8: profile-name normalisation lives in the assemble module
# (single source of truth for the legacy theme → profile alias map +
# the unknown-name fallback). Importing here would create a cycle
# (assemble imports nothing from config), so we re-declare the alias
# table locally — it's two entries and changes lock-step with the
# yaml registry.
_SLIDE_PROFILE_LEGACY_ALIASES = {
    "gold": "default",
    "metropolis": "metropolis_minimal",
}
_KNOWN_SLIDE_PROFILES = {"default", "metropolis_minimal"}
_DEFAULT_SLIDE_PROFILE = "default"


def _resolve_slide_profile_env(raw: str) -> str:
    norm = (raw or "").strip().lower()
    norm = _SLIDE_PROFILE_LEGACY_ALIASES.get(norm, norm)
    if norm in _KNOWN_SLIDE_PROFILES:
        return norm
    return _DEFAULT_SLIDE_PROFILE


@dataclass(frozen=True)
class Settings:
    # ── 2. Workspace + storage ──────────────────────────────────────────
    workspace_dir: Path
    db_path: Path
    papers_cache_dir: Path
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
    # SQL Agent ReAct loop model — reason→query→curate→finalize (small tier).
    sql_agent_model: str
    # Memory-add conflict detector (small tier; classifier-shaped).
    memory_conflict_model: str

    # Per-call LLM request timeout (seconds). Bounds EVERY litellm completion so
    # a slow/unavailable provider can't ride the litellm default (~600 s) ×
    # retries and hang a turn. The adapter uses it to fail the primary (flagship)
    # call FAST and downgrade to the small tier; also set as litellm.request_timeout
    # at boot as a backstop.
    llm_timeout_s: float

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

    # ── 10. External lookup services (optional) ──────────────────────────
    # Unpaywall fallback for the ss: dispatch — when SS has no
    # openAccessPdf.url, we query Unpaywall by DOI to find a free PDF on
    # the publisher's site / a preprint mirror. Unpaywall REQUIRES a
    # contact email in the query string (for abuse-control logging — they
    # don't spam). When None, the fallback is skipped and behaviour
    # reverts to F4.2 (NoIngestibleSourceError for non-arXiv papers
    # without an SS-indexed PDF URL).
    unpaywall_email: str | None

    # ── 11. Slide presentation (Beamer preamble profile) ───────────────
    # F4.4 T8: default preamble profile name for generated decks. The
    # registry is yaml-driven (``slide_style_profiles.yaml`` in
    # ``pipelines/slide_pipeline``); ``"default"`` ships as the
    # Final_Report gold methodology, ``"metropolis_minimal"`` as the
    # legacy minimal preamble. Operators edit the yaml — or add new
    # profiles — without code changes. The env-var path accepts the new
    # ``PAPERHUB_SLIDE_STYLE_PROFILE`` (preferred) AND the legacy
    # ``PAPERHUB_SLIDE_THEME`` (alias mapping ``gold→default``,
    # ``metropolis→metropolis_minimal``). Unknown env values normalise
    # to ``"default"`` so a stray typo never silently emits an unrelated
    # style. The resolved name is also what ``decks.theme`` stores
    # (backward-compat with dashboards / API consumers).
    slide_style_profile: str

    # Fallback deck length (content slides) used by the outline ONLY when the
    # user's request states no length. An explicit count / range in the request
    # (read by the outline LLM, any language) always overrides this. Configurable
    # via PAPERHUB_SLIDE_DEFAULT_LENGTH (.env or the runtime Settings panel).
    slide_default_length: int


_SMALL_TIER_DEFAULT = "gemini/gemini-3.1-flash-lite"
_FLAGSHIP_TIER_DEFAULT = "gemini/gemini-2.5-pro"


def small_tier_model() -> str:
    """Resolve the small-tier LLM model (``PAPERHUB_MODEL_SMALL`` or the
    built-in default). Used as the default for every per-slot env var whose
    workload is a cheap classifier / fast lookup (router, chitchat, paper_qa
    subagent, sql planner, report resolver). Per-slot env vars still win."""
    return os.environ.get("PAPERHUB_MODEL_SMALL") or _SMALL_TIER_DEFAULT


def flagship_tier_model() -> str:
    """Resolve the flagship-tier LLM model (``PAPERHUB_MODEL_FLAGSHIP`` or
    the built-in default). Default for slots that produce user-facing prose
    (paper_qa synthesis, sql answer, slide notes/plan/section). Per-slot
    env vars still win."""
    return os.environ.get("PAPERHUB_MODEL_FLAGSHIP") or _FLAGSHIP_TIER_DEFAULT


def llm_timeout_s() -> float:
    """Per-call LLM timeout in seconds (``PAPERHUB_LLM_TIMEOUT``, default 120).

    Bounds every litellm completion so an unavailable flagship can't ride the
    litellm default (~600 s) × retries. The adapter also uses this to fail the
    primary (flagship) call FAST so it can downgrade to the small tier promptly.
    A non-numeric value falls back to the 120 s default rather than crashing boot."""
    try:
        return float(os.environ.get("PAPERHUB_LLM_TIMEOUT", "120"))
    except ValueError:
        return 120.0


def load_settings() -> Settings:
    workspace = Path(os.environ.get("PAPERHUB_WORKSPACE", "./workspace")).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    small = small_tier_model()
    flagship = flagship_tier_model()
    return Settings(
        # 2. Workspace + storage.
        workspace_dir=workspace,
        db_path=workspace / "paperhub.db",
        papers_cache_dir=workspace / "papers_cache",
        max_upload_mb=int(os.environ.get("PAPERHUB_MAX_UPLOAD_MB", "30")),

        # 3. LLM model selection. Each slot's per-slot env var (e.g.
        # PAPERHUB_ROUTER_MODEL) STILL takes precedence; the tier values
        # are only the default when the per-slot var is unset.
        router_model=os.environ.get("PAPERHUB_ROUTER_MODEL", small),
        chitchat_model=os.environ.get("PAPERHUB_CHITCHAT_MODEL", small),
        paper_qa_model=os.environ.get("PAPERHUB_PAPER_QA_MODEL", flagship),
        paper_qa_subagent_model=os.environ.get(
            "PAPERHUB_PAPER_QA_SUBAGENT_MODEL", small,
        ),
        sql_agent_model=os.environ.get("PAPERHUB_SQL_AGENT_MODEL", small),
        memory_conflict_model=os.environ.get(
            "PAPERHUB_MEMORY_CONFLICT_MODEL", small,
        ),
        llm_timeout_s=llm_timeout_s(),

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

        # 9. Report Agent (slides) model selection — tier defaults; per-slot
        # env vars still win.
        report_plan_model=os.environ.get("PAPERHUB_REPORT_PLAN_MODEL", flagship),
        report_section_model=os.environ.get(
            "PAPERHUB_REPORT_SECTION_MODEL", flagship,
        ),
        report_notes_model=os.environ.get("PAPERHUB_REPORT_NOTES_MODEL", flagship),
        report_resolve_model=os.environ.get("PAPERHUB_REPORT_RESOLVE_MODEL", small),

        # 10. External lookup services.
        unpaywall_email=os.environ.get("PAPERHUB_UNPAYWALL_EMAIL") or None,

        # 11. Slide presentation (Beamer preamble profile).
        # PAPERHUB_SLIDE_STYLE_PROFILE (preferred) wins; PAPERHUB_SLIDE_THEME
        # is the legacy alias (gold→default, metropolis→metropolis_minimal).
        # Unknown values normalise to "default" so a typo never silently
        # emits an unrelated style.
        slide_style_profile=_resolve_slide_profile_env(
            os.environ.get("PAPERHUB_SLIDE_STYLE_PROFILE")
            or os.environ.get("PAPERHUB_SLIDE_THEME")
            or _DEFAULT_SLIDE_PROFILE
        ),
        slide_default_length=int(
            os.environ.get("PAPERHUB_SLIDE_DEFAULT_LENGTH", "15")
        ),
    )
