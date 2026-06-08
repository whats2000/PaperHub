# Plan G — Frontend i18n + Account Menu + Runtime Settings Panel

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Internationalize the frontend chrome (`en` / `zh-TW` / `zh-CN` / `ja`) and add a sidebar-footer account menu that hosts a language switcher, a theme submenu, and a **Settings modal** that edits the backend's `.env`-class config at runtime via a DB-backed overlay.

**Architecture:** Two slices. **(1) Backend settings slice** — a new `settings` table is the durable source of truth, projected as an **overlay onto `os.environ`** so the existing live-reading `load_settings()` (17 call sites) picks changes up with zero call-site edits. A declarative `settings_registry.py` drives `GET`/`PATCH /settings`; secrets are masked write-only; provider credentials are a free-form key→value list. **(2) Frontend** — `react-i18next` scaffold (provider at both Vite entries), a Base-UI `AccountMenu` in the sidebar footer, and a `SettingsModal` driven by the new REST surface, localized via a new `settings` i18n namespace.

**Tech Stack:** Backend — FastAPI, aiosqlite, Pydantic, pytest/ruff/mypy. Frontend — React 19, `@base-ui/react`, `next-themes`, Zustand, `i18next` + `react-i18next` + `i18next-browser-languagedetector`, Vitest + RTL + MSW.

**Spec:** [docs/superpowers/specs/2026-05-17-paperhub-srs.md](../specs/2026-05-17-paperhub-srs.md) — v2.31 changelog entry, FR-13, FR-14, §III-2 (`AccountMenu` / `SettingsModal` rows).

**Conventions:** Conventional Commits. Backend from `backend/`: `uv run pytest <file>`, `uv run ruff check src tests`, `uv run mypy src`. Frontend from `frontend/`: `npm test`, `npm run typecheck`, `npm run lint`. TDD: failing test → minimal impl → commit. Run only the relevant test file per task; full suite + real-API `:8000` gate at plan-phase completion.

**Branch:** create `feat/plan-G-i18n-settings` before Task A1.

---

## File Structure

**Backend (new):**
- `backend/src/paperhub/settings_registry.py` — declarative registry of editable env vars + validation + the provider-credential allowlist.
- `backend/src/paperhub/settings_overlay.py` — the `os.environ` overlay (set/clear/apply, lazy base-capture for revert).
- `backend/src/paperhub/api/settings.py` — `GET`/`PATCH /settings` router.
- `backend/tests/test_settings_registry.py`, `test_settings_overlay.py`, `test_settings_schema.py`, `test_settings_api.py`.

**Backend (modified):**
- `backend/src/paperhub/db/schema.sql` — add the `settings` table.
- `backend/src/paperhub/app.py` — register the router + apply the overlay at boot.

**Frontend (new):**
- `frontend/src/lib/i18n.ts` — i18next init.
- `frontend/src/locales/{en,zh-TW,zh-CN,ja}/{common,chat,references,canvas,slides,memory,states,settings}.json` — catalogs.
- `frontend/src/components/layout/AccountMenu.tsx` — sidebar-footer account button + popover.
- `frontend/src/components/settings/SettingsModal.tsx` — the config modal.
- `frontend/src/store/settings.ts` — modal open-state + config cache + fetch/patch actions.
- Test files colocated (`*.test.tsx`) + `frontend/src/lib/settingsApi.test.ts`.

**Frontend (modified):**
- `frontend/package.json` — i18n deps.
- `frontend/src/main.tsx`, `frontend/src/present/main.tsx` — wrap `<I18nextProvider>`.
- `frontend/src/components/layout/Sidebar.tsx` — mount `AccountMenu` in the footer.
- `frontend/src/lib/api.ts` — `getSettings` / `patchSettings`.
- `frontend/src/App.tsx` — render `SettingsModal` at root.
- `frontend/tests/setup.ts` — init i18n + force `en` + MSW `/settings` handlers.

---

# Part A — Backend settings slice

### Task A0: Branch

- [ ] **Step 1: Create the feature branch**

```bash
git switch -c feat/plan-G-i18n-settings
```

---

### Task A1: `settings` table

**Files:**
- Modify: `backend/src/paperhub/db/schema.sql`
- Test: `backend/tests/test_settings_schema.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_settings_schema.py
import aiosqlite
import pytest

pytestmark = pytest.mark.asyncio


async def test_settings_table_exists(migrated_db: aiosqlite.Connection) -> None:
    async with migrated_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='settings'"
    ) as cur:
        names = {r[0] for r in await cur.fetchall()}
    assert "settings" in names


async def test_settings_key_is_primary_key(migrated_db: aiosqlite.Connection) -> None:
    await migrated_db.execute(
        "INSERT INTO settings (key, value) VALUES ('PAPERHUB_LOG_LEVEL', 'DEBUG')"
    )
    # Second insert of the same key must conflict (PK).
    with pytest.raises(aiosqlite.IntegrityError):
        await migrated_db.execute(
            "INSERT INTO settings (key, value) VALUES ('PAPERHUB_LOG_LEVEL', 'INFO')"
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings_schema.py -v`
Expected: FAIL — `settings` table does not exist.

- [ ] **Step 3: Add the table to the schema**

Append to `backend/src/paperhub/db/schema.sql` (follow the existing `CREATE TABLE IF NOT EXISTS` + `datetime('now')` timestamp convention):

```sql
-- Runtime configuration overlay (Plan G / FR-14). Durable source of truth
-- for editable .env-class config; projected onto os.environ at boot. A row
-- exists ONLY for keys the user overrode in the Settings panel; absence means
-- "fall back to backend/.env / built-in default".
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings_schema.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/db/schema.sql backend/tests/test_settings_schema.py
git commit -m "feat(settings): add settings table for runtime config overlay"
```

---

### Task A2: Settings registry + validation + credential allowlist

**Files:**
- Create: `backend/src/paperhub/settings_registry.py`
- Test: `backend/tests/test_settings_registry.py`

The registry is the single declarative description of every editable var. It drives the GET schema and PATCH validation. Provider credentials are NOT enumerated as fixed fields — they are a free-form category guarded by `is_allowed_credential_key`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_settings_registry.py
import pytest

from paperhub.settings_registry import (
    SETTINGS_REGISTRY,
    SettingField,
    coerce_value,
    field_by_key,
    is_allowed_credential_key,
)


def test_registry_covers_known_categories() -> None:
    categories = {f.category for f in SETTINGS_REGISTRY}
    assert {
        "llm_models",
        "agent_tunables",
        "memory",
        "external_services",
        "external_lookup",
        "storage",
        "logging",
        "marker",
        "slides",
    } <= categories


def test_dead_memory_semantic_is_not_in_registry() -> None:
    # PAPERHUB_MEMORY_SEMANTIC is dead config — omitted from the UI registry.
    assert field_by_key("PAPERHUB_MEMORY_SEMANTIC") is None


def test_int_field_rejects_out_of_range() -> None:
    field = field_by_key("PAPERHUB_PAPER_QA_MAX_SECTION_READS")
    assert field is not None
    with pytest.raises(ValueError):
        coerce_value(field, "0")  # min is 1
    assert coerce_value(field, "8") == "8"


def test_enum_field_rejects_unknown_choice() -> None:
    field = field_by_key("PAPERHUB_LOG_LEVEL")
    assert field is not None and field.restart_required is True
    with pytest.raises(ValueError):
        coerce_value(field, "TRACE")
    assert coerce_value(field, "DEBUG") == "DEBUG"


def test_email_field_validation() -> None:
    field = field_by_key("PAPERHUB_UNPAYWALL_EMAIL")
    assert field is not None
    with pytest.raises(ValueError):
        coerce_value(field, "not-an-email")
    assert coerce_value(field, "a@b.com") == "a@b.com"


def test_secret_field_is_marked() -> None:
    field = field_by_key("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY")
    assert field is not None and field.secret is True


def test_credential_allowlist() -> None:
    assert is_allowed_credential_key("GEMINI_API_KEY")
    assert is_allowed_credential_key("AZURE_API_BASE")
    assert is_allowed_credential_key("OPENROUTER_API_KEY")
    assert is_allowed_credential_key("SOME_NEW_PROVIDER_API_KEY")  # suffix match
    assert not is_allowed_credential_key("PATH")
    assert not is_allowed_credential_key("HOME")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings_registry.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the registry**

```python
# backend/src/paperhub/settings_registry.py
"""Declarative registry of editable .env-class settings (Plan G / FR-14).

The registry is the single source of truth for what the Settings panel can
edit, how each field is validated, and which fields are secrets or require a
restart. Provider credentials are NOT enumerated here — they are a free-form
category guarded by ``is_allowed_credential_key``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

FieldType = Literal["string", "int", "bool", "email", "enum", "secret"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class SettingField:
    key: str
    category: str
    label: str
    type: FieldType
    default: str | None = None
    help: str = ""
    secret: bool = False
    restart_required: bool = False
    read_only: bool = False
    min: int | None = None
    max: int | None = None
    choices: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


# Curated set of known LiteLLM provider env vars (offered as autocomplete in
# the free-form credentials editor). New providers also work via the suffix
# pattern in ``is_allowed_credential_key`` — extend this list to add a
# suggestion, not to unlock a provider.
PROVIDER_CREDENTIAL_SUGGESTIONS: tuple[str, ...] = (
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_API_KEY",
    "AZURE_API_BASE",
    "AZURE_API_VERSION",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
    "GROQ_API_KEY",
    "COHERE_API_KEY",
    "DEEPSEEK_API_KEY",
    "TOGETHERAI_API_KEY",
    "XAI_API_KEY",
    "PERPLEXITYAI_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "VERTEXAI_PROJECT",
    "VERTEXAI_LOCATION",
    "OLLAMA_API_BASE",
)

# A free-form credential key is accepted if it is a known suggestion OR matches
# the credential-shaped suffix pattern. This blocks arbitrary env injection
# (PATH, HOME, …) while letting any real provider env var through.
_CREDENTIAL_SUFFIX_RE = re.compile(
    r"^[A-Z][A-Z0-9_]*_(API_KEY|API_BASE|API_VERSION|KEY|TOKEN|REGION|PROJECT|LOCATION)$"
)


def is_allowed_credential_key(key: str) -> bool:
    return key in PROVIDER_CREDENTIAL_SUGGESTIONS or bool(_CREDENTIAL_SUFFIX_RE.match(key))


_SMALL = "gemini/gemini-3.1-flash-lite"
_FLAGSHIP = "gemini/gemini-2.5-pro"

SETTINGS_REGISTRY: list[SettingField] = [
    # ── LLM model selection ─────────────────────────────────────────────
    SettingField("PAPERHUB_MODEL_SMALL", "llm_models", "Small-tier model", "string",
                 default=_SMALL, help="Default for classifiers / fast tool calls."),
    SettingField("PAPERHUB_MODEL_FLAGSHIP", "llm_models", "Flagship-tier model", "string",
                 default=_FLAGSHIP, help="Default for user-facing prose."),
    SettingField("PAPERHUB_ROUTER_MODEL", "llm_models", "Router model", "string",
                 help="Intent classifier. Defaults to the small tier."),
    SettingField("PAPERHUB_CHITCHAT_MODEL", "llm_models", "Chitchat model", "string"),
    SettingField("PAPERHUB_PAPER_QA_MODEL", "llm_models", "paper_qa finalizer", "string"),
    SettingField("PAPERHUB_PAPER_QA_SUBAGENT_MODEL", "llm_models", "paper_qa subagent", "string"),
    SettingField("PAPERHUB_SQL_AGENT_MODEL", "llm_models", "SQL planner", "string"),
    SettingField("PAPERHUB_SQL_ANSWER_MODEL", "llm_models", "SQL answer", "string"),
    SettingField("PAPERHUB_MEMORY_CONFLICT_MODEL", "llm_models", "Memory conflict detector", "string"),
    SettingField("PAPERHUB_REPORT_RESOLVE_MODEL", "llm_models", "Slide resolver", "string"),
    SettingField("PAPERHUB_REPORT_NOTES_MODEL", "llm_models", "Slide notes author", "string"),
    SettingField("PAPERHUB_REPORT_PLAN_MODEL", "llm_models", "Slide agent", "string"),
    SettingField("PAPERHUB_REPORT_SECTION_MODEL", "llm_models", "Slide single-frame edit", "string"),
    # ── Agent tunables ──────────────────────────────────────────────────
    SettingField("PAPERHUB_PAPER_QA_MAX_SECTION_READS", "agent_tunables",
                 "Max section reads / subagent turn", "int", default="8", min=1, max=50),
    SettingField("PAPERHUB_SESSION_RETENTION_DAYS", "agent_tunables",
                 "Soft-deleted session retention (days)", "int", default="30", min=1, max=3650),
    SettingField("PAPERHUB_MARKER_MAX_PAGES", "marker",
                 "Marker pages per /extract call", "int", default="1", min=1, max=100),
    # ── Memory / recall ─────────────────────────────────────────────────
    SettingField("PAPERHUB_MEMORY_RECALL", "memory", "Inject recalled memories", "bool",
                 default="1", help="Surface active memories to answering agents."),
    # NOTE: PAPERHUB_MEMORY_SEMANTIC is intentionally OMITTED — dead config.
    # ── External services ───────────────────────────────────────────────
    SettingField("PAPERHUB_SEMANTIC_SCHOLAR_API_KEY", "external_services",
                 "Semantic Scholar API key", "secret", secret=True,
                 help="Optional; the unauthenticated tier is rate-limited."),
    # ── External lookup ─────────────────────────────────────────────────
    SettingField("PAPERHUB_UNPAYWALL_EMAIL", "external_lookup", "Unpaywall contact email", "email",
                 help="Enables the DOI→free-PDF fallback. Used for abuse logging only."),
    # ── Storage ─────────────────────────────────────────────────────────
    SettingField("PAPERHUB_MAX_UPLOAD_MB", "storage", "Max PDF upload (MiB)", "int",
                 default="30", min=1, max=500),
    SettingField("PAPERHUB_WORKSPACE", "storage", "Workspace directory", "string",
                 default="./workspace", restart_required=True, read_only=True,
                 help="Set via env var at boot; restart the backend to change."),
    # ── Logging ─────────────────────────────────────────────────────────
    SettingField("PAPERHUB_LOG_LEVEL", "logging", "Log level", "enum", default="INFO",
                 restart_required=True, choices=("DEBUG", "INFO", "WARNING", "ERROR")),
    # ── Marker ──────────────────────────────────────────────────────────
    SettingField("PAPERHUB_MARKER_URL", "marker", "Marker service URL", "string",
                 default="http://127.0.0.1:8002", restart_required=True),
    SettingField("PAPERHUB_INPROCESS_MARKER", "marker", "In-process Marker", "bool",
                 default="0", restart_required=True),
    # ── Slides ──────────────────────────────────────────────────────────
    SettingField("PAPERHUB_SLIDE_STYLE_PROFILE", "slides", "Slide style profile", "enum",
                 default="default", choices=("default", "metropolis_minimal")),
]

_BY_KEY = {f.key: f for f in SETTINGS_REGISTRY}


def field_by_key(key: str) -> SettingField | None:
    return _BY_KEY.get(key)


def coerce_value(field: SettingField, raw: str) -> str:
    """Validate ``raw`` against ``field`` and return the canonical string to
    store. Raises ``ValueError`` on invalid input."""
    if field.read_only:
        raise ValueError(f"{field.key} is read-only (set it via env at boot).")
    value = raw.strip()
    if field.type == "int":
        try:
            n = int(value)
        except ValueError as exc:
            raise ValueError(f"{field.key} must be an integer.") from exc
        if field.min is not None and n < field.min:
            raise ValueError(f"{field.key} must be >= {field.min}.")
        if field.max is not None and n > field.max:
            raise ValueError(f"{field.key} must be <= {field.max}.")
        return str(n)
    if field.type == "bool":
        if value.lower() in ("1", "true", "yes", "on"):
            return "1"
        if value.lower() in ("0", "false", "no", "off"):
            return "0"
        raise ValueError(f"{field.key} must be a boolean (0/1).")
    if field.type == "enum":
        if value not in field.choices:
            raise ValueError(f"{field.key} must be one of {field.choices}.")
        return value
    if field.type == "email":
        if not _EMAIL_RE.match(value):
            raise ValueError(f"{field.key} must be a valid email address.")
        return value
    # string / secret
    if not value:
        raise ValueError(f"{field.key} must not be empty.")
    return value
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings_registry.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Lint + type-check the new module**

Run: `uv run ruff check src/paperhub/settings_registry.py tests/test_settings_registry.py && uv run mypy src/paperhub/settings_registry.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/settings_registry.py backend/tests/test_settings_registry.py
git commit -m "feat(settings): declarative registry + validation + credential allowlist"
```

---

### Task A3: `os.environ` overlay (set/clear/apply + revert)

**Files:**
- Create: `backend/src/paperhub/settings_overlay.py`
- Test: `backend/tests/test_settings_overlay.py`

The overlay captures the pre-override value of each key the FIRST time it is overridden (boot overlay or runtime PATCH), so clearing reverts to the `.env`/default value. `load_settings()` reads `os.environ` live, so mutating it hot-applies.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_settings_overlay.py
import os

import pytest

from paperhub import settings_overlay as ov


@pytest.fixture(autouse=True)
def _isolate_env():
    """Snapshot + restore os.environ and reset the overlay base between tests
    (the overlay mutates process-global os.environ, bypassing monkeypatch)."""
    before = dict(os.environ)
    ov.reset_for_tests()
    yield
    os.environ.clear()
    os.environ.update(before)
    ov.reset_for_tests()


def test_set_then_clear_reverts_to_unset() -> None:
    os.environ.pop("PAPERHUB_LOG_LEVEL", None)
    ov.set_override("PAPERHUB_LOG_LEVEL", "DEBUG")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "DEBUG"
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    assert "PAPERHUB_LOG_LEVEL" not in os.environ


def test_set_then_clear_reverts_to_env_value() -> None:
    os.environ["PAPERHUB_LOG_LEVEL"] = "INFO"  # simulate .env
    ov.set_override("PAPERHUB_LOG_LEVEL", "DEBUG")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "DEBUG"
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "INFO"


def test_apply_overlay_records_base_before_applying() -> None:
    os.environ["PAPERHUB_MODEL_SMALL"] = "from-env"
    ov.apply_overlay({"PAPERHUB_MODEL_SMALL": "from-db"})
    assert os.environ["PAPERHUB_MODEL_SMALL"] == "from-db"
    ov.clear_override("PAPERHUB_MODEL_SMALL")
    assert os.environ["PAPERHUB_MODEL_SMALL"] == "from-env"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings_overlay.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the overlay**

```python
# backend/src/paperhub/settings_overlay.py
"""Project DB-backed settings onto os.environ (Plan G / FR-14).

``load_settings()`` reads os.environ live per request, so mutating it
hot-applies. The first time a key is overridden we record its prior value so
clearing the override reverts to the .env / built-in default.
"""
from __future__ import annotations

import os

# key -> value held by os.environ BEFORE the first override (None = was unset).
_base: dict[str, str | None] = {}


def _record_base(key: str) -> None:
    if key not in _base:
        _base[key] = os.environ.get(key)


def set_override(key: str, value: str) -> None:
    _record_base(key)
    os.environ[key] = value


def clear_override(key: str) -> None:
    original = _base.pop(key, "__absent__")
    if original == "__absent__":
        os.environ.pop(key, None)
    elif original is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = original


def apply_overlay(rows: dict[str, str]) -> None:
    """Apply every DB row onto os.environ (records base first)."""
    for key, value in rows.items():
        set_override(key, value)


def reset_for_tests() -> None:
    _base.clear()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings_overlay.py -v`
Expected: PASS (all three).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/paperhub/settings_overlay.py tests/test_settings_overlay.py && uv run mypy src/paperhub/settings_overlay.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/settings_overlay.py backend/tests/test_settings_overlay.py
git commit -m "feat(settings): os.environ overlay with revert-to-base"
```

---

### Task A4: `GET /settings` endpoint

**Files:**
- Create: `backend/src/paperhub/api/settings.py`
- Modify: `backend/src/paperhub/app.py` (register router)
- Test: `backend/tests/test_settings_api.py`

GET returns the registry grouped by category + current values. Non-secrets return the effective value + `is_default`. Secrets return `is_set` only — the value never round-trips. The `provider_credentials` category is built dynamically from DB rows whose key is an allowed credential key.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_settings_api.py
import os
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from paperhub import settings_overlay as ov
from paperhub.app import create_app
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def settings_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncClient:
    monkeypatch.setenv("PAPERHUB_PREWARM_MODELS", "0")
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("PAPERHUB_BOOT_BANNER", "0")
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
    ov.reset_for_tests()
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c
    ov.reset_for_tests()


async def test_get_settings_returns_categories(settings_client: AsyncClient) -> None:
    resp = await settings_client.get("/settings")
    assert resp.status_code == 200
    body = resp.json()
    cats = {c["key"] for c in body["categories"]}
    assert {"provider_credentials", "llm_models", "logging"} <= cats


async def test_get_settings_masks_secret_value(settings_client: AsyncClient) -> None:
    resp = await settings_client.get("/settings")
    fields = [
        f
        for c in resp.json()["categories"]
        for f in c["fields"]
        if f["key"] == "PAPERHUB_SEMANTIC_SCHOLAR_API_KEY"
    ]
    assert fields and fields[0]["secret"] is True
    assert "value" not in fields[0]  # secret value never returned
    assert "is_set" in fields[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: FAIL — 404 (no `/settings` route).

- [ ] **Step 3: Write the GET router**

```python
# backend/src/paperhub/api/settings.py
"""GET/PATCH /settings — runtime config panel (Plan G / FR-14)."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from paperhub import settings_overlay as ov
from paperhub.config import load_settings
from paperhub.db.connection import open_db
from paperhub.settings_registry import (
    PROVIDER_CREDENTIAL_SUGGESTIONS,
    SETTINGS_REGISTRY,
    coerce_value,
    field_by_key,
    is_allowed_credential_key,
)

router = APIRouter(prefix="/settings", tags=["settings"])

_CATEGORY_LABELS = {
    "provider_credentials": "Provider credentials",
    "llm_models": "LLM models",
    "agent_tunables": "Agent tunables",
    "memory": "Memory / recall",
    "external_services": "External services",
    "external_lookup": "External lookup",
    "storage": "Workspace / storage",
    "logging": "Logging",
    "marker": "Marker",
    "slides": "Slide style",
}

# Order categories deterministically for the modal's left-nav.
_CATEGORY_ORDER = [
    "provider_credentials", "llm_models", "agent_tunables", "memory",
    "external_services", "external_lookup", "storage", "logging", "marker", "slides",
]


async def _db_rows(db_path: Any) -> dict[str, str]:
    async with open_db(db_path) as conn, conn.execute(
        "SELECT key, value FROM settings"
    ) as cur:
        return {r[0]: r[1] for r in await cur.fetchall()}


@router.get("")
async def get_settings() -> dict[str, Any]:
    settings = load_settings()
    rows = await _db_rows(settings.db_path)

    cats: dict[str, list[dict[str, Any]]] = {k: [] for k in _CATEGORY_ORDER}

    # Free-form provider credentials: every DB row that is an allowed credential
    # key (and not a structured registry field). Values are NEVER returned.
    for key in sorted(rows):
        if field_by_key(key) is None and is_allowed_credential_key(key):
            cats["provider_credentials"].append(
                {"key": key, "label": key, "type": "secret",
                 "secret": True, "is_set": True, "restart_required": False}
            )

    # Structured fields from the registry.
    for f in SETTINGS_REGISTRY:
        effective = os.environ.get(f.key, f.default)
        item: dict[str, Any] = {
            "key": f.key, "label": f.label, "type": f.type,
            "secret": f.secret, "restart_required": f.restart_required,
            "read_only": f.read_only, "help": f.help,
            "is_default": f.key not in rows,
        }
        if f.choices:
            item["choices"] = list(f.choices)
        if f.min is not None:
            item["min"] = f.min
        if f.max is not None:
            item["max"] = f.max
        if f.secret:
            item["is_set"] = bool(effective)
        else:
            item["value"] = effective
        cats[f.category].append(item)

    return {
        "categories": [
            {
                "key": c,
                "label": _CATEGORY_LABELS[c],
                "free_form": c == "provider_credentials",
                "suggestions": list(PROVIDER_CREDENTIAL_SUGGESTIONS)
                if c == "provider_credentials" else [],
                "fields": cats[c],
            }
            for c in _CATEGORY_ORDER
        ]
    }
```

- [ ] **Step 4: Register the router in `app.py`**

In `backend/src/paperhub/app.py`, near the other `app.include_router(...)` calls (around lines 266-272), add the import alongside the existing API imports and include it:

```python
from paperhub.api import settings as settings_api
...
app.include_router(settings_api.router)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: PASS (both GET tests).

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/api/settings.py backend/src/paperhub/app.py backend/tests/test_settings_api.py
git commit -m "feat(settings): GET /settings returns masked registry by category"
```

---

### Task A5: `PATCH /settings` endpoint

**Files:**
- Modify: `backend/src/paperhub/api/settings.py`
- Test: `backend/tests/test_settings_api.py` (extend)

PATCH validates each key against the registry (or the credential allowlist), upserts/clears the DB row, and mutates `os.environ`. Returns updated/cleared keys + which need a restart.

- [ ] **Step 1: Write the failing test (append to `test_settings_api.py`)**

```python
async def test_patch_hot_applies_non_secret(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch(
        "/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "12"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "PAPERHUB_PAPER_QA_MAX_SECTION_READS" in body["updated"]
    # Hot-applied: load_settings reflects it immediately.
    from paperhub.config import load_settings
    assert load_settings().paper_qa_max_section_reads == 12
    # GET now reports it as non-default.
    got = await settings_client.get("/settings")
    field = [
        f for c in got.json()["categories"] for f in c["fields"]
        if f["key"] == "PAPERHUB_PAPER_QA_MAX_SECTION_READS"
    ][0]
    assert field["value"] == "12" and field["is_default"] is False


async def test_patch_flags_restart_required(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch("/settings", json={"PAPERHUB_LOG_LEVEL": "DEBUG"})
    assert resp.status_code == 200
    assert "PAPERHUB_LOG_LEVEL" in resp.json()["restart_required"]


async def test_patch_rejects_invalid_value(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch(
        "/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "0"}
    )
    assert resp.status_code == 422


async def test_patch_rejects_unknown_key(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch("/settings", json={"PATH": "/evil"})
    assert resp.status_code == 422


async def test_patch_credential_is_write_only(settings_client: AsyncClient) -> None:
    resp = await settings_client.patch("/settings", json={"OPENAI_API_KEY": "sk-secret"})
    assert resp.status_code == 200
    got = await settings_client.get("/settings")
    cred_cat = [c for c in got.json()["categories"] if c["key"] == "provider_credentials"][0]
    field = [f for f in cred_cat["fields"] if f["key"] == "OPENAI_API_KEY"][0]
    assert field["is_set"] is True
    assert "value" not in field  # never echoed


async def test_patch_clear_reverts_to_default(settings_client: AsyncClient) -> None:
    await settings_client.patch("/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": "20"})
    resp = await settings_client.patch(
        "/settings", json={"PAPERHUB_PAPER_QA_MAX_SECTION_READS": None}
    )
    assert resp.status_code == 200
    assert "PAPERHUB_PAPER_QA_MAX_SECTION_READS" in resp.json()["cleared"]
    from paperhub.config import load_settings
    assert load_settings().paper_qa_max_section_reads == 8  # default restored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: FAIL — 405 (no PATCH handler).

- [ ] **Step 3: Add the PATCH handler to `api/settings.py`**

```python
class SettingsPatch(BaseModel):
    # {key: value|null}; null/empty clears the override.
    __root__: dict[str, str | None] = {}


@router.patch("")
async def patch_settings(body: dict[str, str | None]) -> dict[str, Any]:
    updated: list[str] = []
    cleared: list[str] = []
    restart: list[str] = []

    # Validate + coerce ALL keys first (reject the whole request on any error).
    to_set: dict[str, str] = {}
    to_clear: list[str] = []
    for key, raw in body.items():
        field = field_by_key(key)
        is_cred = field is None and is_allowed_credential_key(key)
        if field is None and not is_cred:
            raise HTTPException(422, f"Unknown or non-editable setting: {key}")
        if field is not None and field.read_only:
            raise HTTPException(422, f"{key} is read-only.")
        if raw is None or raw.strip() == "":
            to_clear.append(key)
            continue
        if field is not None:
            try:
                to_set[key] = coerce_value(field, raw)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
        else:  # credential: opaque non-empty secret
            to_set[key] = raw.strip()

    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        for key, value in to_set.items():
            await conn.execute(
                "INSERT INTO settings (key, value, updated_at) "
                "VALUES (?, ?, datetime('now')) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=datetime('now')",
                (key, value),
            )
            ov.set_override(key, value)
            updated.append(key)
            f = field_by_key(key)
            if f is not None and f.restart_required:
                restart.append(key)
        for key in to_clear:
            await conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            ov.clear_override(key)
            cleared.append(key)
        await conn.commit()

    return {"updated": updated, "cleared": cleared, "restart_required": restart}
```

Note: delete the unused `SettingsPatch` model if `ruff`/`mypy` flags it — the handler takes the raw `dict` directly (Pydantic root models differ across versions; the flat `dict[str, str | None]` body is the stable form). Remove the `BaseModel` import if no longer used.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings_api.py -v`
Expected: PASS (all PATCH + GET tests).

- [ ] **Step 5: Lint + type-check**

Run: `uv run ruff check src/paperhub/api/settings.py tests/test_settings_api.py && uv run mypy src/paperhub/api/settings.py`
Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/api/settings.py backend/tests/test_settings_api.py
git commit -m "feat(settings): PATCH /settings validates, hot-applies, clears"
```

---

### Task A6: Apply the overlay at boot

**Files:**
- Modify: `backend/src/paperhub/app.py` (lifespan)
- Test: `backend/tests/test_settings_boot.py`

So a setting persisted in the DB is re-applied to `os.environ` on the next backend start.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_settings_boot.py
import os
from pathlib import Path

import aiosqlite
import pytest

from paperhub import settings_overlay as ov
from paperhub.app import apply_settings_overlay_at_boot
from paperhub.db.migrate import apply_schema

pytestmark = pytest.mark.asyncio


async def test_boot_overlay_applies_db_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "paperhub.db"
    async with aiosqlite.connect(db_path) as conn:
        await apply_schema(conn)
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ('PAPERHUB_LOG_LEVEL', 'WARNING')"
        )
        await conn.commit()
    monkeypatch.delenv("PAPERHUB_LOG_LEVEL", raising=False)
    ov.reset_for_tests()
    async with aiosqlite.connect(db_path) as conn:
        await apply_settings_overlay_at_boot(conn)
    assert os.environ["PAPERHUB_LOG_LEVEL"] == "WARNING"
    ov.clear_override("PAPERHUB_LOG_LEVEL")
    ov.reset_for_tests()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_settings_boot.py -v`
Expected: FAIL — `apply_settings_overlay_at_boot` not defined.

- [ ] **Step 3: Add the helper + call it in the lifespan**

In `backend/src/paperhub/app.py`, add a module-level coroutine:

```python
from paperhub import settings_overlay as ov

async def apply_settings_overlay_at_boot(conn: "aiosqlite.Connection") -> None:
    """Project persisted settings rows onto os.environ at startup (FR-14)."""
    async with conn.execute("SELECT key, value FROM settings") as cur:
        rows = {r[0]: r[1] for r in await cur.fetchall()}
    ov.apply_overlay(rows)
```

Then call it inside `_lifespan`, immediately after `await apply_schema(conn)` (around line 63), using the same `conn`:

```python
await apply_schema(conn)
await apply_settings_overlay_at_boot(conn)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_settings_boot.py -v`
Expected: PASS.

- [ ] **Step 5: Run the whole settings backend slice + lint/type**

Run: `uv run pytest tests/test_settings_schema.py tests/test_settings_registry.py tests/test_settings_overlay.py tests/test_settings_api.py tests/test_settings_boot.py -v && uv run ruff check src tests && uv run mypy src`
Expected: all PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add backend/src/paperhub/app.py backend/tests/test_settings_boot.py
git commit -m "feat(settings): apply DB overlay onto os.environ at boot"
```

---

# Part B — Frontend i18n scaffold + Account Menu

### Task B1: i18n scaffold + providers + test init

**Files:**
- Modify: `frontend/package.json` (deps)
- Create: `frontend/src/lib/i18n.ts`
- Create: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/common.json`
- Modify: `frontend/src/main.tsx`, `frontend/src/present/main.tsx`
- Modify: `frontend/tests/setup.ts`
- Test: `frontend/src/lib/i18n.test.ts`

- [ ] **Step 1: Install deps**

Run (from `frontend/`):

```bash
npm install i18next react-i18next i18next-browser-languagedetector
```

- [ ] **Step 2: Write the failing test**

```typescript
// frontend/src/lib/i18n.test.ts
import { describe, expect, it } from "vitest";
import i18n from "./i18n";

describe("i18n", () => {
  it("defaults to en and resolves a common key", async () => {
    await i18n.changeLanguage("en");
    expect(i18n.t("common:appName")).toBe("PaperHub");
  });

  it("switches to zh-TW", async () => {
    await i18n.changeLanguage("zh-TW");
    expect(i18n.t("common:language")).toBe("語言");
    await i18n.changeLanguage("en");
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- src/lib/i18n.test.ts`
Expected: FAIL — cannot resolve `./i18n`.

- [ ] **Step 4: Create the catalogs**

`frontend/src/locales/en/common.json`:

```json
{
  "appName": "PaperHub",
  "language": "Language",
  "theme": "Theme",
  "themeLight": "Light",
  "themeDark": "Dark",
  "themeSystem": "System",
  "settings": "Settings",
  "about": "About",
  "account": "Account",
  "save": "Save",
  "cancel": "Cancel",
  "close": "Close"
}
```

`frontend/src/locales/zh-TW/common.json`:

```json
{
  "appName": "PaperHub",
  "language": "語言",
  "theme": "主題",
  "themeLight": "淺色",
  "themeDark": "深色",
  "themeSystem": "跟隨系統",
  "settings": "設定",
  "about": "關於",
  "account": "帳號",
  "save": "儲存",
  "cancel": "取消",
  "close": "關閉"
}
```

`frontend/src/locales/zh-CN/common.json`:

```json
{
  "appName": "PaperHub",
  "language": "语言",
  "theme": "主题",
  "themeLight": "浅色",
  "themeDark": "深色",
  "themeSystem": "跟随系统",
  "settings": "设置",
  "about": "关于",
  "account": "账号",
  "save": "保存",
  "cancel": "取消",
  "close": "关闭"
}
```

`frontend/src/locales/ja/common.json`:

```json
{
  "appName": "PaperHub",
  "language": "言語",
  "theme": "テーマ",
  "themeLight": "ライト",
  "themeDark": "ダーク",
  "themeSystem": "システム",
  "settings": "設定",
  "about": "情報",
  "account": "アカウント",
  "save": "保存",
  "cancel": "キャンセル",
  "close": "閉じる"
}
```

- [ ] **Step 5: Create `src/lib/i18n.ts`**

```typescript
// frontend/src/lib/i18n.ts
import i18n from "i18next";
import LanguageDetector from "i18next-browser-languagedetector";
import { initReactI18next } from "react-i18next";

import enCommon from "../locales/en/common.json";
import jaCommon from "../locales/ja/common.json";
import zhCNCommon from "../locales/zh-CN/common.json";
import zhTWCommon from "../locales/zh-TW/common.json";

export const SUPPORTED_LANGUAGES = ["en", "zh-TW", "zh-CN", "ja"] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const LANGUAGE_ENDONYMS: Record<SupportedLanguage, string> = {
  en: "English",
  "zh-TW": "繁體中文",
  "zh-CN": "简体中文",
  ja: "日本語",
};

// English is the source-of-truth catalog. New namespaces are added here and
// to each locale folder as the string-extraction pass progresses (Task D1).
const resources = {
  en: { common: enCommon },
  "zh-TW": { common: zhTWCommon },
  "zh-CN": { common: zhCNCommon },
  ja: { common: jaCommon },
} as const;

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources,
    fallbackLng: "en",
    supportedLngs: SUPPORTED_LANGUAGES as unknown as string[],
    ns: ["common"],
    defaultNS: "common",
    interpolation: { escapeValue: false },
    detection: {
      order: ["localStorage", "navigator"],
      lookupLocalStorage: "paperhub-lang",
      caches: ["localStorage"],
    },
  });

export default i18n;
```

- [ ] **Step 6: Wrap both Vite entries**

In `frontend/src/main.tsx`, import the i18n side-effect module and wrap the tree:

```typescript
import { I18nextProvider } from "react-i18next";
import i18n from "./lib/i18n";
// ... inside render, wrap <App/> (and the existing ThemeProvider):
//   <I18nextProvider i18n={i18n}>...existing tree...</I18nextProvider>
```

Do the same in `frontend/src/present/main.tsx` (wrap `<PresentPage .../>` and the fallback `<div>` in `<I18nextProvider i18n={i18n}>`).

- [ ] **Step 7: Force `en` in the test setup**

In `frontend/tests/setup.ts`, add at the top (after the existing polyfills):

```typescript
import i18n from "../src/lib/i18n";

// Existing getByText assertions match the English source catalog.
beforeEach(() => {
  void i18n.changeLanguage("en");
});
```

(Import `beforeEach` from `vitest` if not already imported.)

- [ ] **Step 8: Run test to verify it passes**

Run: `npm test -- src/lib/i18n.test.ts`
Expected: PASS.

- [ ] **Step 9: Typecheck + lint + build**

Run: `npm run typecheck && npm run lint && npm run build`
Expected: clean (the `import ... .json` needs `resolveJsonModule` — it is on by default in Vite's tsconfig; if typecheck errors, set `"resolveJsonModule": true` in `tsconfig.json`).

- [ ] **Step 10: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/lib/i18n.ts frontend/src/lib/i18n.test.ts frontend/src/locales frontend/src/main.tsx frontend/src/present/main.tsx frontend/tests/setup.ts
git commit -m "feat(i18n): scaffold react-i18next + common catalogs + provider"
```

---

### Task B2: AccountMenu in the sidebar footer (Language + Theme + About + Settings)

**Files:**
- Create: `frontend/src/components/layout/AccountMenu.tsx`
- Modify: `frontend/src/components/layout/Sidebar.tsx` (mount in footer)
- Test: `frontend/src/components/layout/AccountMenu.test.tsx`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/components/layout/AccountMenu.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import i18n from "../../lib/i18n";
import { AccountMenu } from "./AccountMenu";

describe("AccountMenu", () => {
  it("opens and switches the UI language", async () => {
    const user = userEvent.setup();
    render(<AccountMenu collapsed={false} onOpenSettings={() => {}} />);
    await user.click(screen.getByRole("button", { name: /account/i }));
    // The Language label is visible (English source catalog).
    expect(screen.getByText("Language")).toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "日本語" }));
    expect(i18n.language).toBe("ja");
    await i18n.changeLanguage("en");
  });

  it("invokes onOpenSettings when Settings is clicked", async () => {
    const user = userEvent.setup();
    let opened = false;
    render(<AccountMenu collapsed={false} onOpenSettings={() => (opened = true)} />);
    await user.click(screen.getByRole("button", { name: /account/i }));
    await user.click(screen.getByRole("menuitem", { name: "Settings" }));
    expect(opened).toBe(true);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/components/layout/AccountMenu.test.tsx`
Expected: FAIL — cannot resolve `./AccountMenu`.

- [ ] **Step 3: Write the component**

Use `@base-ui/react` `Menu` primitives (the codebase already depends on `@base-ui/react`; check an existing usage for the exact import path, e.g. `import { Menu } from "@base-ui/react/menu"`). Mirror `ThemeToggle.tsx` for the theme options.

```tsx
// frontend/src/components/layout/AccountMenu.tsx
import { Menu } from "@base-ui/react/menu";
import { Check, Monitor, Moon, Settings as SettingsIcon, Sun, User } from "lucide-react";
import { useTheme } from "next-themes";
import { useTranslation } from "react-i18next";

import {
  LANGUAGE_ENDONYMS,
  SUPPORTED_LANGUAGES,
  type SupportedLanguage,
} from "../../lib/i18n";

interface Props {
  collapsed: boolean;
  onOpenSettings: () => void;
}

const THEME_OPTIONS = [
  { value: "light", icon: Sun, key: "themeLight" },
  { value: "dark", icon: Moon, key: "themeDark" },
  { value: "system", icon: Monitor, key: "themeSystem" },
] as const;

export function AccountMenu({ collapsed, onOpenSettings }: Props) {
  const { t, i18n } = useTranslation("common");
  const { theme, setTheme } = useTheme();

  return (
    <Menu.Root>
      <Menu.Trigger
        aria-label={t("account")}
        className="flex w-full items-center gap-2 rounded-md px-2 py-2 hover:bg-muted"
      >
        <span className="grid size-7 place-items-center rounded-full bg-muted">
          <User className="size-4" />
        </span>
        {!collapsed && <span className="text-sm">{t("account")}</span>}
      </Menu.Trigger>
      <Menu.Portal>
        <Menu.Positioner side="top" align="start">
          <Menu.Popup className="min-w-48 rounded-md border bg-popover p-1 shadow-md">
            {/* Language submenu */}
            <Menu.SubmenuRoot>
              <Menu.SubmenuTrigger className="menu-item">{t("language")}</Menu.SubmenuTrigger>
              <Menu.Portal>
                <Menu.Positioner>
                  <Menu.Popup className="min-w-40 rounded-md border bg-popover p-1 shadow-md">
                    {SUPPORTED_LANGUAGES.map((lng: SupportedLanguage) => (
                      <Menu.Item
                        key={lng}
                        className="menu-item flex items-center justify-between"
                        onClick={() => void i18n.changeLanguage(lng)}
                      >
                        <span>{LANGUAGE_ENDONYMS[lng]}</span>
                        {i18n.language === lng && <Check className="size-4" />}
                      </Menu.Item>
                    ))}
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.SubmenuRoot>

            {/* Theme submenu */}
            <Menu.SubmenuRoot>
              <Menu.SubmenuTrigger className="menu-item">{t("theme")}</Menu.SubmenuTrigger>
              <Menu.Portal>
                <Menu.Positioner>
                  <Menu.Popup className="min-w-40 rounded-md border bg-popover p-1 shadow-md">
                    {THEME_OPTIONS.map(({ value, icon: Icon, key }) => (
                      <Menu.Item
                        key={value}
                        className="menu-item flex items-center justify-between"
                        onClick={() => setTheme(value)}
                      >
                        <span className="flex items-center gap-2">
                          <Icon className="size-4" />
                          {t(key)}
                        </span>
                        {theme === value && <Check className="size-4" />}
                      </Menu.Item>
                    ))}
                  </Menu.Popup>
                </Menu.Positioner>
              </Menu.Portal>
            </Menu.SubmenuRoot>

            <Menu.Separator className="my-1 h-px bg-border" />

            <Menu.Item
              className="menu-item flex items-center gap-2"
              onClick={onOpenSettings}
            >
              <SettingsIcon className="size-4" />
              {t("settings")}
            </Menu.Item>

            <Menu.Item className="menu-item flex items-center gap-2" disabled>
              {t("about")} · v{__APP_VERSION__ ?? ""}
            </Menu.Item>
          </Menu.Popup>
        </Menu.Positioner>
      </Menu.Portal>
    </Menu.Root>
  );
}
```

Notes for the implementer:
- Verify the exact `@base-ui/react` Menu import path + subcomponent names against an existing component that already uses Base UI menus/popovers in the repo (grep `@base-ui/react`). If `SubmenuRoot`/`SubmenuTrigger` differ in this version, use the version's submenu primitive; the test only asserts on roles/labels.
- `__APP_VERSION__`: if the repo does not already define a build-time version global, replace the About line with a static string or read `import.meta.env.VITE_APP_VERSION`. Drop the `?? ""` accordingly. Keep the `menuitem` role + visible "About" text so tests pass.
- Ensure each `Menu.Item` exposes `role="menuitem"` (Base UI does by default) so the test's `getByRole("menuitem", ...)` resolves.

- [ ] **Step 4: Mount in the sidebar footer**

In `frontend/src/components/layout/Sidebar.tsx`, just before the sidebar's closing `</div>` (around line 271), add the footer:

```tsx
<div className="mt-auto border-t p-2">
  <AccountMenu collapsed={collapsed} onOpenSettings={openSettings} />
</div>
```

Wire `openSettings` from the settings store (Task C2): `const openSettings = useSettingsStore((s) => s.open);`. Until Task C2 lands, pass `onOpenSettings={() => {}}` and replace it in C3. Import `AccountMenu`. Confirm `collapsed` is the existing prop/state name the Sidebar uses for its collapsed state (grep the file).

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test -- src/components/layout/AccountMenu.test.tsx`
Expected: PASS.

- [ ] **Step 6: Typecheck + lint**

Run: `npm run typecheck && npm run lint`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/layout/AccountMenu.tsx frontend/src/components/layout/AccountMenu.test.tsx frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(i18n): sidebar-footer account menu (language/theme/settings)"
```

---

# Part C — Settings modal

### Task C1: API client `getSettings` / `patchSettings`

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/src/lib/settingsApi.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/lib/settingsApi.test.ts
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { API_BASE_URL, getSettings, patchSettings } from "./api";

const server = setupServer(
  http.get(`${API_BASE_URL}/settings`, () =>
    HttpResponse.json({
      categories: [
        { key: "logging", label: "Logging", free_form: false, suggestions: [],
          fields: [{ key: "PAPERHUB_LOG_LEVEL", label: "Log level", type: "enum",
            value: "INFO", choices: ["DEBUG", "INFO"], secret: false,
            restart_required: true, read_only: false, is_default: true }] },
      ],
    }),
  ),
  http.patch(`${API_BASE_URL}/settings`, () =>
    HttpResponse.json({ updated: ["PAPERHUB_LOG_LEVEL"], cleared: [], restart_required: ["PAPERHUB_LOG_LEVEL"] }),
  ),
);

describe("settings api", () => {
  beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => server.close());

  it("getSettings returns categories", async () => {
    const cfg = await getSettings();
    expect(cfg.categories[0].key).toBe("logging");
  });

  it("patchSettings returns restart_required", async () => {
    const res = await patchSettings({ PAPERHUB_LOG_LEVEL: "DEBUG" });
    expect(res.restart_required).toContain("PAPERHUB_LOG_LEVEL");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/lib/settingsApi.test.ts`
Expected: FAIL — `getSettings`/`patchSettings` not exported.

- [ ] **Step 3: Add the client functions + types to `api.ts`**

```typescript
// Append to frontend/src/lib/api.ts

export interface SettingsField {
  key: string;
  label: string;
  type: "string" | "int" | "bool" | "email" | "enum" | "secret";
  value?: string | null;
  is_set?: boolean;
  is_default?: boolean;
  secret: boolean;
  restart_required: boolean;
  read_only?: boolean;
  help?: string;
  choices?: string[];
  min?: number;
  max?: number;
}

export interface SettingsCategory {
  key: string;
  label: string;
  free_form: boolean;
  suggestions: string[];
  fields: SettingsField[];
}

export interface SettingsConfig {
  categories: SettingsCategory[];
}

export interface SettingsPatchResult {
  updated: string[];
  cleared: string[];
  restart_required: string[];
}

export async function getSettings(): Promise<SettingsConfig> {
  return apiFetch<SettingsConfig>(`/settings`);
}

export async function patchSettings(
  patch: Record<string, string | null>,
): Promise<SettingsPatchResult> {
  return apiFetch<SettingsPatchResult>(`/settings`, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/lib/settingsApi.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/settingsApi.test.ts
git commit -m "feat(settings): frontend api client getSettings/patchSettings"
```

---

### Task C2: Settings store (open-state + config cache + actions)

**Files:**
- Create: `frontend/src/store/settings.ts`
- Test: `frontend/src/store/settings.test.ts`

- [ ] **Step 1: Write the failing test**

```typescript
// frontend/src/store/settings.test.ts
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { API_BASE_URL } from "../lib/api";
import { useSettingsStore } from "./settings";

const server = setupServer(
  http.get(`${API_BASE_URL}/settings`, () =>
    HttpResponse.json({ categories: [{ key: "logging", label: "Logging", free_form: false, suggestions: [], fields: [] }] }),
  ),
);

describe("settings store", () => {
  beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => server.close());

  it("open() sets isOpen and fetch loads the config", async () => {
    useSettingsStore.getState().open();
    expect(useSettingsStore.getState().isOpen).toBe(true);
    await useSettingsStore.getState().fetchConfig();
    expect(useSettingsStore.getState().config?.categories[0].key).toBe("logging");
    useSettingsStore.getState().close();
    expect(useSettingsStore.getState().isOpen).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/store/settings.test.ts`
Expected: FAIL — cannot resolve `./settings`.

- [ ] **Step 3: Write the store**

```typescript
// frontend/src/store/settings.ts
import { create } from "zustand";

import { getSettings, patchSettings, type SettingsConfig } from "../lib/api";

interface SettingsState {
  isOpen: boolean;
  config: SettingsConfig | null;
  loading: boolean;
  restartPending: string[];
  open: () => void;
  close: () => void;
  fetchConfig: () => Promise<void>;
  save: (patch: Record<string, string | null>) => Promise<void>;
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  isOpen: false,
  config: null,
  loading: false,
  restartPending: [],
  open: () => {
    set({ isOpen: true });
    if (!get().config) void get().fetchConfig();
  },
  close: () => set({ isOpen: false }),
  fetchConfig: async () => {
    set({ loading: true });
    try {
      const config = await getSettings();
      set({ config });
    } finally {
      set({ loading: false });
    }
  },
  save: async (patch) => {
    const res = await patchSettings(patch);
    set((s) => ({
      restartPending: Array.from(new Set([...s.restartPending, ...res.restart_required])),
    }));
    await get().fetchConfig(); // refresh masked/effective values
  },
}));
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- src/store/settings.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/settings.ts frontend/src/store/settings.test.ts
git commit -m "feat(settings): zustand store for the settings modal"
```

---

### Task C3: SettingsModal component + wire into the app

**Files:**
- Create: `frontend/src/components/settings/SettingsModal.tsx`
- Modify: `frontend/src/App.tsx` (render at root)
- Modify: `frontend/src/components/layout/Sidebar.tsx` (replace the `onOpenSettings` stub with the store opener)
- Test: `frontend/src/components/settings/SettingsModal.test.tsx`

- [ ] **Step 1: Write the failing test**

```tsx
// frontend/src/components/settings/SettingsModal.test.tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";

import { API_BASE_URL } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";
import { SettingsModal } from "./SettingsModal";

const server = setupServer(
  http.get(`${API_BASE_URL}/settings`, () =>
    HttpResponse.json({
      categories: [
        { key: "external_services", label: "External services", free_form: false, suggestions: [],
          fields: [{ key: "PAPERHUB_SEMANTIC_SCHOLAR_API_KEY", label: "Semantic Scholar API key",
            type: "secret", secret: true, is_set: false, restart_required: false }] },
        { key: "logging", label: "Logging", free_form: false, suggestions: [],
          fields: [{ key: "PAPERHUB_LOG_LEVEL", label: "Log level", type: "enum", value: "INFO",
            choices: ["DEBUG", "INFO"], secret: false, restart_required: true, is_default: true }] },
      ],
    }),
  ),
);

describe("SettingsModal", () => {
  beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
  afterAll(() => server.close());
  beforeEach(() => useSettingsStore.setState({ isOpen: true, config: null, restartPending: [] }));

  it("renders categories and a masked secret field", async () => {
    render(<SettingsModal />);
    expect(await screen.findByText("External services")).toBeInTheDocument();
    await userEvent.click(screen.getByText("External services"));
    // Secret renders as not-set with a Replace affordance, never a value.
    expect(screen.getByText(/not set/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- src/components/settings/SettingsModal.test.tsx`
Expected: FAIL — cannot resolve `./SettingsModal`.

- [ ] **Step 3: Write the modal**

Use the `@base-ui/react` `Dialog` primitive (grep the repo for an existing Dialog usage; if none, a portal + fixed overlay is acceptable). Left-nav of categories + a field panel. Render one widget per field type. The component reads/writes the settings store.

```tsx
// frontend/src/components/settings/SettingsModal.tsx
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { SettingsField } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";

export function SettingsModal() {
  const { t } = useTranslation(["common", "settings"]);
  const { isOpen, config, restartPending, close, fetchConfig, save } = useSettingsStore();
  const [activeCat, setActiveCat] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && !config) void fetchConfig();
  }, [isOpen, config, fetchConfig]);

  useEffect(() => {
    if (config && !activeCat) setActiveCat(config.categories[0]?.key ?? null);
  }, [config, activeCat]);

  if (!isOpen) return null;
  const current = config?.categories.find((c) => c.key === activeCat);

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/40" role="dialog" aria-modal="true">
      <div className="flex h-[70vh] w-[840px] max-w-[92vw] overflow-hidden rounded-lg border bg-background shadow-xl">
        {/* Left nav */}
        <nav className="w-56 shrink-0 overflow-y-auto border-r p-2">
          <h2 className="px-2 py-1 text-sm font-semibold">{t("common:settings")}</h2>
          {config?.categories.map((c) => (
            <button
              key={c.key}
              onClick={() => setActiveCat(c.key)}
              className={`block w-full rounded px-2 py-1.5 text-left text-sm ${
                c.key === activeCat ? "bg-muted font-medium" : "hover:bg-muted/60"
              }`}
            >
              {c.label}
            </button>
          ))}
        </nav>
        {/* Field panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-between border-b p-3">
            <span className="font-medium">{current?.label}</span>
            <button onClick={close} className="rounded px-2 py-1 text-sm hover:bg-muted">
              {t("common:close")}
            </button>
          </div>
          {restartPending.length > 0 && (
            <div className="border-b bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
              {t("settings:restartPending", "Restart the backend to apply: {{keys}}", {
                keys: restartPending.join(", "),
              })}
            </div>
          )}
          <div className="flex-1 overflow-y-auto p-3">
            {current?.free_form ? (
              <CredentialEditor suggestions={current.suggestions} fields={current.fields} onSave={save} />
            ) : (
              current?.fields.map((f) => <FieldRow key={f.key} field={f} onSave={save} />)
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function FieldRow({
  field,
  onSave,
}: {
  field: SettingsField;
  onSave: (patch: Record<string, string | null>) => Promise<void>;
}) {
  const { t } = useTranslation("common");
  const [draft, setDraft] = useState<string>(field.value ?? "");
  const [replacing, setReplacing] = useState(false);

  if (field.read_only) {
    return (
      <div className="mb-4">
        <label className="text-sm font-medium">{field.label}</label>
        <input
          readOnly
          value={field.value ?? ""}
          className="mt-1 w-full rounded border bg-muted px-2 py-1 text-sm"
        />
        {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
      </div>
    );
  }

  if (field.secret) {
    return (
      <div className="mb-4">
        <label className="text-sm font-medium">
          {field.label} {field.restart_required && <RestartBadge />}
        </label>
        {replacing ? (
          <div className="mt-1 flex gap-2">
            <input
              type="password"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm"
            />
            <button
              onClick={() => void onSave({ [field.key]: draft }).then(() => setReplacing(false))}
              className="rounded bg-primary px-3 text-sm text-primary-foreground"
            >
              {t("save")}
            </button>
          </div>
        ) : (
          <div className="mt-1 flex items-center gap-2 text-sm">
            <span className={field.is_set ? "text-green-600" : "text-muted-foreground"}>
              {field.is_set ? t("setIndicator", "••• set") : t("notSet", "not set")}
            </span>
            <button onClick={() => setReplacing(true)} className="rounded border px-2 py-0.5 text-xs">
              {t("replace", "Replace")}
            </button>
          </div>
        )}
        {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
      </div>
    );
  }

  // string / int / email / enum / bool
  return (
    <div className="mb-4">
      <label className="text-sm font-medium">
        {field.label} {field.restart_required && <RestartBadge />}
      </label>
      <div className="mt-1 flex gap-2">
        {field.type === "enum" ? (
          <select value={draft} onChange={(e) => setDraft(e.target.value)} className="w-full rounded border px-2 py-1 text-sm">
            {field.choices?.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        ) : field.type === "bool" ? (
          <select value={draft || "0"} onChange={(e) => setDraft(e.target.value)} className="w-full rounded border px-2 py-1 text-sm">
            <option value="1">on</option>
            <option value="0">off</option>
          </select>
        ) : (
          <input
            type={field.type === "int" ? "number" : "text"}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded border px-2 py-1 text-sm"
          />
        )}
        <button
          onClick={() => void onSave({ [field.key]: draft === "" ? null : draft })}
          className="rounded bg-primary px-3 text-sm text-primary-foreground"
        >
          {t("save")}
        </button>
      </div>
      {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
    </div>
  );
}

function RestartBadge() {
  const { t } = useTranslation("settings");
  return (
    <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-900 dark:text-amber-200">
      {t("restartBadge", "Restart to apply")}
    </span>
  );
}

function CredentialEditor({
  suggestions,
  fields,
  onSave,
}: {
  suggestions: string[];
  fields: SettingsField[];
  onSave: (patch: Record<string, string | null>) => Promise<void>;
}) {
  const { t } = useTranslation("settings");
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  return (
    <div>
      <ul className="mb-4 space-y-1">
        {fields.map((f) => (
          <li key={f.key} className="flex items-center justify-between rounded border px-2 py-1 text-sm">
            <span className="font-mono">{f.key}</span>
            <button onClick={() => void onSave({ [f.key]: null })} className="text-xs text-red-600">
              {t("remove", "Remove")}
            </button>
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <input
          list="cred-suggestions"
          placeholder={t("providerKeyPlaceholder", "PROVIDER_API_KEY")}
          value={newKey}
          onChange={(e) => setNewKey(e.target.value.toUpperCase())}
          className="w-1/2 rounded border px-2 py-1 font-mono text-sm"
        />
        <datalist id="cred-suggestions">
          {suggestions.map((s) => <option key={s} value={s} />)}
        </datalist>
        <input
          type="password"
          placeholder={t("valuePlaceholder", "value")}
          value={newVal}
          onChange={(e) => setNewVal(e.target.value)}
          className="w-1/2 rounded border px-2 py-1 text-sm"
        />
        <button
          disabled={!newKey || !newVal}
          onClick={() => void onSave({ [newKey]: newVal }).then(() => { setNewKey(""); setNewVal(""); })}
          className="rounded bg-primary px-3 text-sm text-primary-foreground disabled:opacity-50"
        >
          {t("add", "Add")}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Render at the app root + wire the opener**

In `frontend/src/App.tsx`, render `<SettingsModal />` once at the root (it self-gates on `isOpen`):

```tsx
import { SettingsModal } from "./components/settings/SettingsModal";
// inside the returned tree, after <Shell>...</Shell>:
<SettingsModal />
```

In `frontend/src/components/layout/Sidebar.tsx`, replace the Task B2 stub with the store opener:

```tsx
import { useSettingsStore } from "../../store/settings";
// ...
const openSettings = useSettingsStore((s) => s.open);
// <AccountMenu collapsed={collapsed} onOpenSettings={openSettings} />
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test -- src/components/settings/SettingsModal.test.tsx`
Expected: PASS.

- [ ] **Step 6: Typecheck + lint + build**

Run: `npm run typecheck && npm run lint && npm run build`
Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/settings/SettingsModal.tsx frontend/src/components/settings/SettingsModal.test.tsx frontend/src/App.tsx frontend/src/components/layout/Sidebar.tsx
git commit -m "feat(settings): SettingsModal with masked secrets + credential editor"
```

---

### Task C4: `settings` i18n namespace catalogs

**Files:**
- Create: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/settings.json`
- Modify: `frontend/src/lib/i18n.ts` (register the `settings` namespace)

- [ ] **Step 1: Create the English catalog**

`frontend/src/locales/en/settings.json`:

```json
{
  "restartBadge": "Restart to apply",
  "restartPending": "Restart the backend to apply: {{keys}}",
  "replace": "Replace",
  "remove": "Remove",
  "add": "Add",
  "setIndicator": "••• set",
  "notSet": "not set",
  "providerKeyPlaceholder": "PROVIDER_API_KEY",
  "valuePlaceholder": "value"
}
```

Create the same keys translated in `zh-TW`, `zh-CN`, and `ja` (e.g. zh-TW `restartBadge`: `"重新啟動以套用"`, `notSet`: `"未設定"`, `replace`: `"更換"`, `remove`: `"移除"`, `add`: `"新增"`; flag for native-speaker review).

- [ ] **Step 2: Register the namespace in `i18n.ts`**

Import the four `settings.json` files and add `settings: <locale>Settings` to each locale block in `resources`, and add `"settings"` to the `ns` array.

- [ ] **Step 3: Run the i18n + modal tests**

Run: `npm test -- src/lib/i18n.test.ts src/components/settings/SettingsModal.test.tsx`
Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/locales frontend/src/lib/i18n.ts
git commit -m "feat(i18n): settings namespace catalogs (en/zh-TW/zh-CN/ja)"
```

---

# Part D — UI string-extraction pass

### Task D1: Extract chrome strings per namespace

This is the iterative, mechanical part. Do it **one namespace at a time**, following the recipe; each namespace is its own commit. Because the Vitest setup forces `en` (Task B1 Step 7) and English is the source-of-truth catalog, existing `getByText("…")` assertions keep passing — no test churn.

**Namespaces (mirror the component folders):** `chat`, `references`, `canvas`, `slides`, `memory`, `states`. (`common` + `settings` already done.)

**Recipe (per namespace):**

- [ ] **Step 1:** Identify the user-facing literal strings in that feature's components (buttons, labels, tooltips, panel titles, toasts, empty/error/loading states, intent labels). Leave literal: agent-trace tool identifiers (`paper_search:resolve`, …), `[chunk:N]` markers, paper titles, code.
- [ ] **Step 2:** Add keys to `src/locales/en/<namespace>.json` with stable identifiers (e.g. `chat.composer.send` → key `composer.send`). Author `zh-TW` / `zh-CN` / `ja` alongside, flagged for native-speaker review.
- [ ] **Step 3:** Register the namespace in `src/lib/i18n.ts` (import the four JSONs, add to `resources` per locale, add to `ns`).
- [ ] **Step 4:** Replace literals in the components with `const { t } = useTranslation("<namespace>")` + `t("key")`. For interpolation use `t("key", { count })`.
- [ ] **Step 5:** Run that feature's existing tests + typecheck: `npm test -- src/components/<feature>` then `npm run typecheck`. Expected: PASS (English catalog returns the same strings).
- [ ] **Step 6:** Add ONE assertion that switching language changes a visible label in that feature (render the component, `i18n.changeLanguage("ja")`, assert the Japanese string appears, reset to `en`).
- [ ] **Step 7:** Commit: `git commit -m "feat(i18n): localize <namespace> chrome"`.

**Worked example — `chat` namespace, Composer send button:**

`src/locales/en/chat.json`:

```json
{ "composer": { "send": "Send", "placeholder": "Ask about your papers…" } }
```

In `i18n.ts`: import `enChat from "../locales/en/chat.json"` (and the three others), add `chat: enChat` to each locale block, add `"chat"` to `ns`.

In `Composer.tsx`:

```tsx
import { useTranslation } from "react-i18next";
// ...
const { t } = useTranslation("chat");
// replace the literal "Send" / placeholder:
//   aria-label={t("composer.send")}
//   placeholder={t("composer.placeholder")}
```

Switcher assertion (add to `Composer.test.tsx`):

```tsx
it("localizes the send label", async () => {
  const { default: i18n } = await import("../../lib/i18n");
  await i18n.changeLanguage("ja");
  render(<Composer onSubmit={() => {}} disabled={false} />);
  expect(screen.getByLabelText("送信")).toBeInTheDocument();
  await i18n.changeLanguage("en");
});
```

(Use the actual Japanese string you authored for `composer.send`.)

---

# Plan-completion gate (run once, after all tasks)

- [ ] **Backend full suite + quality gates** — from `backend/`: `uv run pytest -v && uv run ruff check src tests && uv run mypy src`. Expected: all green.
- [ ] **Frontend full suite + quality gates** — from `frontend/`: `npm test && npm run typecheck && npm run lint && npm run build`. Expected: all green.
- [ ] **Real-API `:8000` gate** (per CLAUDE.md — required at plan-phase completion, NOT per task). Confirm `:8000` is live (`curl -s -m 3 http://127.0.0.1:8000/health`); if not, ASK the user to start it. Then exercise the settings surface as a user would:
  1. `GET /settings` → secrets masked (`is_set` only, no value), categories present.
  2. `PATCH /settings` `{"PAPERHUB_PAPER_QA_MAX_SECTION_READS":"12"}` → 200; a follow-up `paper_qa` turn's trace (`SELECT result_summary_json FROM tool_calls WHERE run_id = ?`) shows the subagent honoring the new cap.
  3. `PATCH /settings` `{"PAPERHUB_LOG_LEVEL":"DEBUG"}` → `restart_required` includes the key.
  4. `PATCH /settings` `{"OPENROUTER_API_KEY":"sk-test"}` → `GET` shows it set, value never echoed; then clear it.
- [ ] **Frontend visual sign-off** — ASK the user to open the frontend: the account button sits in the sidebar footer; the Language submenu switches the chrome (and a Chinese question still answers in Chinese — i18n is decoupled); the Settings modal opens, a secret shows set/not-set + Replace, a restart-bound save shows the badge, and a free-form provider key can be added/removed.

---

# Self-review notes (against the spec)

- **FR-13 (i18n)** — Tasks B1, B2, D1 (scaffold, account menu language switcher, per-namespace extraction; both Vite entries wrapped; detection order; en source-of-truth).
- **FR-14 (runtime settings)** — Tasks A1–A6 (table, registry, overlay, REST, boot apply) + C1–C4 (client, store, modal, settings namespace). Secrets masked write-only (A4/A5/C3); free-form credentials guarded by the allowlist (A2/A5/C3); workspace/DB paths read-only (A2 registry `read_only`, C3 `FieldRow` read-only branch); restart-bound badge (A2 `restart_required`, C3 `RestartBadge`); dead `PAPERHUB_MEMORY_SEMANTIC` omitted (A2 test asserts it).
- **§III-2** — `AccountMenu` (B2) + `SettingsModal` (C3) component rows.
- **Precedence (default < .env < DB)** — overlay records base on first override and reverts on clear (A3); boot re-applies DB rows (A6).
- **Type consistency** — backend GET shape (`categories[].fields[]` with `key/label/type/value?/is_set?/secret/restart_required/read_only?/choices?/min?/max?/is_default?`) matches the frontend `SettingsField`/`SettingsCategory` types (C1) and the modal's rendering (C3).
