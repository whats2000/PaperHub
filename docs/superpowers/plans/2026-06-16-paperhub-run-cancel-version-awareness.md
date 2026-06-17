# Run Cancellation + Version/Changelog Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v2.37.0 — (A) a **Stop button** that *instantly* retracts an in-flight chat turn and actually stops the backend generation, and (B) a localized in-app changelog + "you just updated" toast + an optional GitHub update-available check.

**Architecture:** Two independent features touching disjoint code except shared i18n + the SRS. (A) — the load-bearing insight (learned the hard way, see "Lessons" below): **the frontend must respond synchronously and immediately on click** — never wait for the abort to propagate back through `fetchEventSource`. On Stop, in one synchronous Zustand update, the turn is **retracted** (the streaming assistant bubble AND its paired user message are removed — never leave a user message without a response) and the user's text is dropped back into the composer; the stream is aborted; and `POST /chat/cancel` tells the backend to **cancel the running asyncio task** (interrupting the live LLM call) and **delete the orphaned turn's message** so it can't reappear on reload. (B) ships a bundled `changelog.json` (localized, en-fallback), a `GET /version` endpoint that does a cached GitHub latest-release lookup gated by a settings toggle, a `ChangelogModal`, and a one-time announce toast.

**Lessons baked into Part A (do NOT repeat these mistakes):**
1. **Immediacy is the whole feature.** The bug was never "cancel doesn't work" — the backend cancel always worked. It was that the UI didn't react *instantly*, so users clicked again, and a restore-then-resend path turned the second click into a duplicate send. The fix is a single synchronous store mutation on click. Do not rely on `AbortController` → `fetchEventSource` → catch; that library may *resolve* (not reject) on abort, leaving the bubble stuck "streaming".
2. **Pair invariant.** A user message must NEVER exist without a paired assistant response — in the UI *and* in the DB. Retract removes both client-side; `/chat/cancel` deletes the server-side row.
3. **No auto-reconnect / no over-engineering.** `/chat` is one-shot (the existing `onerror`-throws already prevents `fetch-event-source` retry). Do NOT add: partial-text persistence, an "explicit-cancel set", a silent-hang `onClose` handler, a reconnect sentinel, or a "Stopped" message bubble. The turn is *removed*, not shown as stopped.
4. **Verify with a LIVE test, not just pytest.** pytest proves wiring; only a real `:8000` run proves the LLM call stack actually stops. Ship `scripts/live_abort_test.py`.

**Tech Stack:** Backend — FastAPI, aiosqlite, httpx, `importlib.metadata`. Frontend — React 19 + TS strict, Zustand, react-i18next, Sonner, Base-UI Dialog, lucide-react. Tests — pytest (backend), Vitest + RTL + MSW (frontend).

**Spec:** SRS FR-15 (run cancellation) + FR-16 (version/changelog/update awareness), `docs/superpowers/specs/2026-05-17-paperhub-srs.md`.

**Per-task gates (CLAUDE.md):** backend from `backend/` via `uv run`; frontend from `frontend/` via `npm`. Run only the touched test files + targeted `ruff`/`mypy`/`typecheck`/`lint` per task; full suites at plan-phase completion. Conventional Commits; focused per-concern commits; never stage build output.

---

## File Structure

**Part A — Run cancellation (Stop):**
- `backend/src/paperhub/api/chat.py` (modify) — a `_running_tasks[run_id]` registry (register the streaming task; pop in `finally`); a new `POST /chat/cancel` that `task.cancel()`s the run, `DELETE`s its messages, and marks the run `cancelled`. NO `_finalise_cancelled`, NO partial accumulation, NO `except CancelledError` finalize.
- `backend/tests/test_chat_cancel.py` (create) — unit: endpoint cancels the task + deletes the turn's message + marks the run cancelled; a bare disconnect leaves it `running`.
- `backend/scripts/live_abort_test.py` (create) — operator live test (real `:8000`): cancel mid-LLM-call → stream stops + run cancelled.
- `frontend/src/store/chat.ts` (modify) — one `retractTurn(sessionId): string` action (remove the trailing assistant + paired user message; return the user text). No `cancelled` status, no "stopped" bubble.
- `frontend/src/lib/api.ts` (modify) — `cancelRun(runId)` → `POST /chat/cancel`.
- `frontend/src/hooks/useChatStream.ts` (modify) — track `runIdRef`/`sessionIdRef`; `stop()` synchronously: abort + `retractTurn` → `requestComposerText` + fire-and-forget `cancelRun`; swallow the user-stop abort error.
- `frontend/src/components/chat/Composer.tsx` (modify) — Send→Stop (square, `type="button"`, tooltip + aria-label) while streaming.
- `frontend/src/pages/ChatPage.tsx` (modify) — wire `isStreaming` + `onStop={stop}`.
- `frontend/src/locales/{en,zh-TW,zh-CN,ja}/chat.json` (modify) — `composer.stop`, `composer.stopTooltip`.
- **Pair invariant on reload/cross-device (A6):** `backend/src/paperhub/api/sessions.py` (modify — `GET …/messages` returns each row's run `status`); `frontend/src/store/chat.ts` (`hydrateSessionMessages` appends a processing placeholder for a trailing `running` user message); `frontend/src/hooks/useChatStream.ts` (`stop()` resolves `run_id` from the streaming message when there's no live `runIdRef`).
- (NOT touched: `MessageBubble.tsx`, `ChatMessage.status` — the cancelled turn is removed, never rendered.)

**Part B — Version / changelog / update-check:**
- `backend/src/paperhub/settings_registry.py` (modify) — `PAPERHUB_UPDATE_CHECK` bool field.
- `backend/src/paperhub/api/version.py` (create) — `GET /version` + `_fetch_latest_release` + TTL cache.
- `backend/src/paperhub/app.py` (modify) — register the version router.
- `backend/tests/test_version_endpoint.py` (create).
- `frontend/nginx.conf` (modify) — proxy `/version`.
- `frontend/src/changelog/changelog.json` (create) — localized release entries.
- `frontend/src/lib/changelog.ts` (create) — typed loader + `localizedHighlights` + `semverGt`.
- `frontend/src/types/domain.ts` (modify) — `VersionInfo`, `ChangelogEntry`.
- `frontend/src/lib/api.ts` (modify) — `getVersion()`.
- `frontend/src/store/version.ts` (create) — `info` + `changelogOpen` + `fetchVersion`.
- `frontend/src/locales/{en,zh-TW,zh-CN,ja}/about.json` (create) — modal chrome.
- `frontend/src/lib/i18n.ts` (modify) — register the `about` namespace.
- `frontend/src/hooks/useVersionAnnounce.ts` (create) — one-time toast.
- `frontend/src/components/about/ChangelogModal.tsx` (create).
- `frontend/src/components/layout/AccountMenu.tsx` (modify) — About → opens modal; update dot.
- `frontend/src/App.tsx` (modify) — fetch version once + `useVersionAnnounce()` + mount `<ChangelogModal/>`.
- `.claude/skills/paperhub-merge-prep/SKILL.md` (modify) — add the changelog-entry step.
- `frontend/tests/...` — colocated tests per component/hook.

---

# Part A — Run Cancellation (Stop)

**Design (per the Lessons above): the click does everything synchronously on the client; the backend kills the run + removes the orphan turn. No "cancelled" message is shown — the turn is *removed*.**

**PAIR INVARIANT (hard rule, drives A1/A3/A6).** A user message must NEVER be displayed alone. Every user message is paired with an assistant element in exactly one of three states:
1. **error** — the assistant message holds an error (run `error`);
2. **valid response** — the assistant message holds the answer (run `ok`);
3. **processing** — a live "generating" placeholder (run `running`), which is **itself abortable**, including from a *different device/tab* (Stop → `POST /chat/cancel {run_id}`).

Consequences:
- Same-tab Stop → retract removes BOTH (no orphan), `/chat/cancel` deletes the server rows (no orphan on reload).
- Reload / second device while a turn is `running` → hydrate as *user message + processing placeholder* (state 3), NOT a bare user message — and the Stop button must work on it via its `run_id` (Task A6).
- The live abort test must assert the LLM stack actually stops: **zero token events after the cancel AND zero new `tool_calls` rows for the run in the seconds after** (not merely that the stream closed).

### Task A1: Backend — `POST /chat/cancel` (kill the task + delete the orphan turn)

**Files:** Modify `backend/src/paperhub/api/chat.py`; Create `backend/tests/test_chat_cancel.py`; Create `backend/scripts/live_abort_test.py`.

**Step 1 — task registry.** Module-level in `chat.py` (ensure `import asyncio`):

```python
# run_id -> the asyncio task streaming that run, so POST /chat/cancel can cancel
# it (which interrupts the live LLM call at once). Process-local — fine for the
# single-worker deployment. Popped in the stream's `finally`.
_running_tasks: dict[int, "asyncio.Task[Any]"] = {}
```

**Step 2 — register + clean up.** In `stream_events()`, immediately after `run_id = await _new_run(conn, session_id)`:

```python
            task = asyncio.current_task()
            if task is not None:
                _running_tasks[run_id] = task
```

In the existing `finally:` block (the one that calls `reset_client_headers_context(headers_token)`), add:

```python
                _running_tasks.pop(run_id, None)
```

Do **NOT** add an `except asyncio.CancelledError` in the generator, no partial-token accumulation, no `_finalise_cancelled`. Let `CancelledError` propagate — the endpoint owns the DB side.

**Step 3 — the endpoint** (add near the bottom of `chat.py`, after `EventSourceResponse(stream_events())` returns):

```python
class CancelRequest(BaseModel):
    run_id: int


@router.post("/chat/cancel")
async def cancel_run(req: CancelRequest) -> dict[str, str]:
    """Explicit user Stop (FR-15). THREE effects so the turn stops at once and
    leaves no orphan:
      (1) cancel the running asyncio task — interrupts the live LLM call NOW;
      (2) DELETE the turn's messages — a user message must never be left without
          a response (it would otherwise reappear on reload / cross-device);
      (3) mark the run 'cancelled' (only while still 'running').
    A bare disconnect (reload, network drop, teardown) never calls this, so it
    is never cancelled."""
    task = _running_tasks.get(req.run_id)
    if task is not None and not task.done():
        task.cancel()
    settings = load_settings()
    async with open_db(settings.db_path) as conn:
        await conn.execute("DELETE FROM messages WHERE run_id = ?", (req.run_id,))
        await conn.execute(
            "UPDATE runs SET finished_at = datetime('now'), status = 'cancelled' "
            "WHERE id = ? AND status = 'running'",
            (req.run_id,),
        )
        await conn.commit()
    return {"status": "cancelled", "run_id": str(req.run_id)}
```

**Step 4 — unit tests** (`test_chat_cancel.py`), with an autouse fixture clearing `_running_tasks` between tests:
- `cancel_run` cancels a registered task (an `asyncio.sleep(30)` stand-in) — assert `task.cancelled()`.
- `cancel_run` deletes the run's messages and sets `runs.status='cancelled'` (seed a `running` run + a user message, call it, assert).
- A `running` run that is NOT cancelled stays `running` (no endpoint call).

**Step 5 — live test** (`scripts/live_abort_test.py`): open a real `/chat` SSE against `:8000`, capture the `run_id` from the `session` event, `POST /chat/cancel` ~4s in (mid-generation). Assert ALL of:
- the stream stops promptly (server closes within ~1s of the cancel);
- **zero token events arrive after the cancel** (the LLM output stopped);
- **the LLM stack actually halts**: snapshot `SELECT COUNT(*) FROM tool_calls WHERE run_id=?` right after the cancel, wait ~5s, snapshot again — the count must be **unchanged** (no new agent/LLM steps fired after the abort);
- `runs.status='cancelled'` and `SELECT COUNT(*) FROM messages WHERE run_id=?` is `0` (the orphan turn was deleted).

This is the REQUIRED proof — pytest does not exercise the real LLM stack.

Gates: `uv run pytest tests/test_chat_cancel.py`, `uv run ruff check src tests`, `uv run mypy src`.
Commit: `feat(chat): POST /chat/cancel kills the run + deletes the orphaned turn (FR-15)`.

### Task A2: Frontend store — `retractTurn`

**Files:** Modify `frontend/src/store/chat.ts`; Test `frontend/tests/store/retractTurn.test.ts`.

Interface: `retractTurn: (sessionId: number) => string;`

```typescript
retractTurn: (sessionId) => {
  let restored = "";
  set((s) => ({
    sessions: s.sessions.map((sess) => {
      if (sess.id !== sessionId) return sess;
      const msgs = [...sess.messages];
      // Drop the trailing streaming assistant placeholder...
      if (msgs.length > 0 && msgs[msgs.length - 1]!.role === "assistant") {
        msgs.pop();
      }
      // ...and the paired user message (never leave a user message without a
      // response), returning its text so the caller restores it to the composer.
      if (msgs.length > 0 && msgs[msgs.length - 1]!.role === "user") {
        restored = msgs[msgs.length - 1]!.content;
        msgs.pop();
      }
      return { ...sess, messages: msgs };
    }),
  }));
  return restored;
},
```

Test: append `[user "hi", assistant "" streaming]`, call `retractTurn(sid)` → `messages.length === 0` and the return value `=== "hi"`.

Commit: `feat(chat): retractTurn store action removes the in-flight pair (FR-15)`.

### Task A3: Frontend hook — synchronous `stop()` (the crux)

**Files:** Modify `frontend/src/lib/api.ts`; Modify `frontend/src/hooks/useChatStream.ts`; Test `frontend/tests/hooks/useChatStreamStop.test.ts`.

`api.ts`:

```typescript
/** Explicit user Stop (FR-15): cancel the backend run. */
export async function cancelRun(runId: number): Promise<void> {
  await apiFetch<{ status: string }>(`/chat/cancel`, {
    method: "POST",
    body: JSON.stringify({ run_id: runId }),
  });
}
```

`useChatStream.ts`:
- Add refs near `abortRef`: `const userStoppedRef = useRef(false); const runIdRef = useRef<number|null>(null); const sessionIdRef = useRef<number|null>(null);`
- At the start of `send`: `userStoppedRef.current = false; runIdRef.current = null; sessionIdRef.current = sessionId;`
- Wherever `runId` is first assigned from an event (`session`/`tool_step`/`routing_decision`/`search_results`), also set `runIdRef.current = runId;`
- Replace the return with `{ send, stop }`, and add:

```typescript
  const stop = useCallback(() => {
    userStoppedRef.current = true;
    const rid = runIdRef.current;
    const sid = sessionIdRef.current;
    // SYNCHRONOUS + IMMEDIATE — the UI must react in this same tick. Do NOT wait
    // for the abort to propagate through fetchEventSource (it may resolve, not
    // reject, on abort). 1) cut the stream, 2) retract the in-flight pair and
    // drop the user's text back into the composer (instant exit from the
    // "generating" state — and a user message is never left without a reply),
    // 3) tell the backend to stop the run + delete its orphaned message.
    abortRef.current?.abort();
    if (sid !== null) {
      const restored = store.getState().retractTurn(sid);
      if (restored) store.getState().requestComposerText(restored);
    }
    if (rid !== null) void cancelRun(rid).catch(() => undefined);
  }, [store]);
```

- In the outer `catch (err)`: add at the top `if (userStoppedRef.current) return;` (the turn was already retracted; swallow the abort). Keep the existing non-stop error handling below it.
- Do NOT add an `onClose` handler, a `streamCompleted` flag, or any reconnect sentinel in `sse.ts`.

Test (the key one): mock `@/lib/sse` so `streamChat` emits `session`+`token` then returns a promise that **resolves** on abort (the worst case). After `send` + `stop()`, assert the session has **0 messages** and `composerDraft === "hello"`. Mock `@/lib/api` `cancelRun` to a resolved `vi.fn`.

Commit: `feat(chat): synchronous stop() retracts the turn instantly + cancels the run (FR-15)`.

### Task A4: Composer — Stop button (+ tooltip, all four locales)

**Files:** Modify `frontend/src/components/chat/Composer.tsx`; Modify `frontend/src/locales/{en,zh-TW,zh-CN,ja}/chat.json`; Test `frontend/tests/components/ComposerStop.test.tsx`.

- Props: `isStreaming?: boolean`, `onStop?: () => void`.
- While `isStreaming`, render a Stop button instead of Send — `type="button"`, `onClick={onStop}`, `aria-label={t("composer.stop")}`, a `<Square>` icon, NOT disabled — wrapped in the same `Tooltip`/`TooltipTrigger`/`TooltipContent` pattern the other composer buttons use, with `t("composer.stopTooltip")`.
- i18n (under `composer`): `stop` = en "Stop" / zh-TW·zh-CN "停止" / ja "停止"; `stopTooltip` = en "Stop generating" / zh-TW·zh-CN "停止生成" / ja "生成を停止".

Test: with `isStreaming`, a `/stop/i` button shows and calls `onStop`; idle shows Send, not Stop. Parity test stays green.

Commit: `feat(chat): Composer Stop button + tooltip while streaming (FR-15)`.

### Task A5: Wire Stop through ChatPage

**Files:** Modify `frontend/src/pages/ChatPage.tsx`.

`const { send, stop } = useChatStream();` and pass `isStreaming={isStreaming}` + `onStop={stop}` to `<Composer>` (the existing `isStreaming` boolean already drives `disabled`).

Commit: `feat(chat): wire the Stop control through ChatPage (FR-15)`.

### Task A6: Pair invariant on reload / cross-device (processing state + abortable)

**Why:** the live store always appends `[user, assistant(streaming)]` together, so a same-tab turn never orphans. The orphan only appears on **hydration from the DB** (`GET /sessions/{id}/messages`): a `running` run has a user row but no assistant row yet. Per the PAIR INVARIANT it must render as *user + processing placeholder* (state 3), abortable.

**Files:** Modify `backend/src/paperhub/api/sessions.py` (the `GET …/messages` handler) + `frontend/src/store/chat.ts` (`hydrateSessionMessages`) + `frontend/src/hooks/useChatStream.ts` (`stop()` run_id resolution); Tests alongside.

1. **Backend** — `GET …/messages` already returns each message's `run_id`. Add the run's `status` to the payload (join `runs`), so the client can tell `running` (→ processing) from a finished run. (Backend already inserts an assistant `error` row for failed runs via `_finalise(status="error")`, satisfying state 1; `ok` runs have the assistant row, state 2.)

2. **Frontend `hydrateSessionMessages`** — when the LAST mapped message is a `user` whose `run_id`'s run is `running` and has no following assistant row, append a synthetic assistant placeholder `{ role:"assistant", content:"", run_id, status:"streaming" }`. This satisfies the invariant (no bare user message) and makes `isStreaming` true so the Stop button shows.

3. **Frontend `stop()` run_id resolution** — a hydrated/cross-device processing turn has no live `runIdRef`. `stop()` resolves the run to cancel as `runIdRef.current ?? <run_id of the trailing streaming assistant message in the active session>`. With that id it calls `cancelRun(rid)` (kills the backend task + deletes the rows) and retracts locally. (`abortRef.current?.abort()` is a harmless no-op when there is no live stream.)

Tests: hydrating a session whose last user message belongs to a `running` run yields a trailing streaming placeholder (not a bare user message); `stop()` with `runIdRef=null` but a streaming message present still calls `cancelRun` with that message's run_id.

Commit: `feat(chat): hydrate running turns as processing + abort across reload/device (FR-15)`.

### Part A verification (REQUIRED — do all before "done")
1. **Unit**: `test_chat_cancel.py` + `retractTurn`/`stop`/`ComposerStop`/hydration tests green; backend ruff+mypy, frontend typecheck+lint clean.
2. **LIVE** (`uv run python scripts/live_abort_test.py` against `:8000`): cancel mid-LLM-call → stream closes ≤1s, **zero tokens after cancel**, **`tool_calls` count unchanged 5s later** (LLM stack halted), run `cancelled`, messages deleted.
3. **Browser** (the gate that actually matters):
   - long turn → **one** Stop click → assistant bubble AND user message vanish instantly, the sentence is back in the input, composer idle, backend stops generating, a second click can't resend;
   - **reload** mid-turn → the turn shows as *user + processing* (state 3), not a bare orphan, and Stop still cancels it;
   - a cancelled turn does NOT reappear on reload (no orphan user message anywhere).
   Ask the user to confirm visually.

---
# Part B — Version / Changelog / Update-check

### Task B1: Backend — `PAPERHUB_UPDATE_CHECK` setting

**Files:**
- Modify: `backend/src/paperhub/settings_registry.py`
- Test: `backend/tests/test_settings_update_check.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_settings_update_check.py`:

```python
from paperhub.settings_registry import coerce_value, field_by_key


def test_update_check_field_exists_and_defaults_on() -> None:
    field = field_by_key("PAPERHUB_UPDATE_CHECK")
    assert field is not None
    assert field.type == "bool"
    assert field.default == "1"


def test_update_check_coerces_bool() -> None:
    field = field_by_key("PAPERHUB_UPDATE_CHECK")
    assert field is not None
    assert coerce_value(field, "off") == "0"
    assert coerce_value(field, "true") == "1"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_settings_update_check.py -v`
Expected: FAIL — `field is None`.

- [ ] **Step 3: Add the registry field**

In `settings_registry.py`, in the `SETTINGS_REGISTRY` list under the External-services block (after the Semantic Scholar / Unpaywall entries, before Storage), add:

```python
    SettingField("PAPERHUB_UPDATE_CHECK", "integrations", "Check for updates", "bool",
                 default="1",
                 help="Let PaperHub ask GitHub whether a newer release exists and "
                      "show an in-app notice. Turn off to disable all outbound "
                      "update checks (no network egress)."),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/test_settings_update_check.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add backend/src/paperhub/settings_registry.py backend/tests/test_settings_update_check.py
git commit -m "feat(settings): add PAPERHUB_UPDATE_CHECK toggle (FR-16)"
```

---

### Task B2: Backend — `GET /version` endpoint

**Files:**
- Create: `backend/src/paperhub/api/version.py`
- Modify: `backend/src/paperhub/app.py:286-293` (router registration)
- Modify: `frontend/nginx.conf`
- Test: `backend/tests/test_version_endpoint.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_version_endpoint.py`:

```python
from httpx import ASGITransport, AsyncClient

import paperhub.api.version as version_mod
from paperhub.app import create_app


async def test_version_reports_current_and_no_check_when_disabled(monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_UPDATE_CHECK", "0")
    version_mod._reset_cache_for_tests()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["current"], str) and body["current"]
    assert body["latest"] is None
    assert body["update_available"] is False


async def test_version_reports_update_available_when_newer(monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_UPDATE_CHECK", "1")
    version_mod._reset_cache_for_tests()

    async def fake_fetch(repo: str):
        return ("999.0.0", f"https://github.com/{repo}/releases/tag/v999.0.0")

    monkeypatch.setattr(version_mod, "_fetch_latest_release", fake_fetch)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    body = resp.json()
    assert body["latest"] == "999.0.0"
    assert body["update_available"] is True
    assert body["html_url"].endswith("v999.0.0")


async def test_version_swallows_fetch_errors(monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_UPDATE_CHECK", "1")
    version_mod._reset_cache_for_tests()

    async def boom(repo: str):
        raise RuntimeError("network down")

    monkeypatch.setattr(version_mod, "_fetch_latest_release", boom)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/version")
    assert resp.status_code == 200
    assert resp.json()["latest"] is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_version_endpoint.py -v`
Expected: FAIL — `ModuleNotFoundError: paperhub.api.version`.

- [ ] **Step 3: Implement the endpoint**

Create `backend/src/paperhub/api/version.py`:

```python
"""GET /version — running version + optional GitHub update check (FR-16).

PaperHub is self-hosted; this endpoint only informs (it never self-updates).
The GitHub lookup is gated by PAPERHUB_UPDATE_CHECK, short-timeout, TTL-cached,
and failure-swallowing — a network problem never breaks the endpoint.
"""
from __future__ import annotations

import os
import time
from importlib.metadata import PackageNotFoundError, version as pkg_version

import httpx
from fastapi import APIRouter

router = APIRouter()

_DEFAULT_REPO = "whats2000/PaperHub"
_CACHE_TTL_SECONDS = 6 * 60 * 60  # 6h

# Module-level cache: (latest, html_url, checked_at_iso, monotonic_expiry).
_cache: tuple[str | None, str | None, str | None, float] | None = None


def _reset_cache_for_tests() -> None:
    global _cache
    _cache = None


def _current_version() -> str:
    try:
        return pkg_version("paperhub")
    except PackageNotFoundError:  # pragma: no cover - dev editable edge
        return "0.0.0"


def _parse_semver(v: str) -> tuple[int, int, int]:
    parts = v.lstrip("v").split(".")[:3]
    nums = []
    for p in parts:
        digits = "".join(ch for ch in p if ch.isdigit())
        nums.append(int(digits) if digits else 0)
    while len(nums) < 3:
        nums.append(0)
    return (nums[0], nums[1], nums[2])


def _is_newer(latest: str, current: str) -> bool:
    return _parse_semver(latest) > _parse_semver(current)


async def _fetch_latest_release(repo: str) -> tuple[str | None, str | None]:
    """Return (latest_version_without_v, html_url) from GitHub, or (None, None).
    Monkeypatched in tests; never raises to the caller in practice."""
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.get(url, headers={"Accept": "application/vnd.github+json"})
        resp.raise_for_status()
        data = resp.json()
    tag = str(data.get("tag_name", "")).lstrip("v")
    html_url = data.get("html_url")
    return (tag or None, html_url)


@router.get("/version")
async def get_version() -> dict[str, object]:
    global _cache
    current = _current_version()
    enabled = os.environ.get("PAPERHUB_UPDATE_CHECK", "1") != "0"
    if not enabled:
        return {
            "current": current,
            "latest": None,
            "update_available": False,
            "html_url": None,
            "checked_at": None,
        }

    now = time.monotonic()
    if _cache is None or now >= _cache[3]:
        latest: str | None = None
        html_url: str | None = None
        try:
            repo = os.environ.get("PAPERHUB_GITHUB_REPO", _DEFAULT_REPO)
            latest, html_url = await _fetch_latest_release(repo)
        except Exception:
            latest, html_url = None, None  # swallow: never break the endpoint
        checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _cache = (latest, html_url, checked_at, now + _CACHE_TTL_SECONDS)

    latest, html_url, checked_at, _ = _cache
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(latest and _is_newer(latest, current)),
        "html_url": html_url,
        "checked_at": checked_at,
    }
```

- [ ] **Step 4: Register the router**

In `backend/src/paperhub/app.py`, add the import alongside the other `api` imports and register it next to the others (~line 293):

```python
    app.include_router(settings_api.router)
    app.include_router(version_api.router)
```

Add the import where the sibling routers are imported (match the existing `from paperhub.api import ... as ..._api` style):

```python
    from paperhub.api import version as version_api
```

(Place it with the other `api` router imports in `create_app`; follow the file's existing import pattern exactly.)

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd backend; uv run pytest tests/test_version_endpoint.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Proxy `/version` in nginx**

In `frontend/nginx.conf`, find the `location` regex that proxies known backend routes (the `(chat|sessions|papers|settings|health|...)` alternation) and add `version` to it. Example — change:

```
location ~ ^/(chat|sessions|papers|chunks|memories|deck|settings|health|mcp.*)/ {
```
to include `version` in the alternation (match the file's actual prefix list; add `|version`). Also ensure the exact `/version` path (no trailing slash) is proxied — if the existing block matches prefixes with a trailing `/`, add a sibling `location = /version { proxy_pass ... }` mirroring how `/health` is handled.

- [ ] **Step 7: Targeted gates + commit**

Run: `cd backend; uv run ruff check src tests; uv run mypy src`
Expected: clean.

```bash
git add backend/src/paperhub/api/version.py backend/src/paperhub/app.py frontend/nginx.conf backend/tests/test_version_endpoint.py
git commit -m "feat(api): GET /version with cached GitHub update check (FR-16)"
```

---

### Task B3: Frontend — bundled changelog data + loader

**Files:**
- Create: `frontend/src/changelog/changelog.json`
- Create: `frontend/src/lib/changelog.ts`
- Modify: `frontend/src/types/domain.ts`
- Test: `frontend/tests/lib/changelog.test.ts`

- [ ] **Step 1: Add the types**

In `frontend/src/types/domain.ts`, append:

```typescript
/** One in-app changelog entry (FR-16). `highlights` is keyed by locale; the
 *  loader falls back to `en` for any locale missing an entry. */
export interface ChangelogEntry {
  version: string;
  date: string;
  highlights: Record<string, string[]>;
}

/** GET /version payload (FR-16). */
export interface VersionInfo {
  current: string;
  latest: string | null;
  update_available: boolean;
  html_url: string | null;
  checked_at: string | null;
}
```

- [ ] **Step 2: Create the bundled changelog**

Create `frontend/src/changelog/changelog.json` (newest-first; en source-of-truth + the three translations for this release):

```json
[
  {
    "version": "2.37.0",
    "date": "2026-06-16",
    "highlights": {
      "en": [
        "Stop button — cancel an in-flight answer; the partial reply is kept and marked Stopped.",
        "What's New + version awareness — this changelog, a one-time toast after you update, and an optional 'update available' notice with the upgrade command."
      ],
      "zh-TW": [
        "停止按鈕 — 可中止生成中的回答；已產生的部分內容會保留並標示為「已停止」。",
        "更新資訊 — 此更新紀錄、更新後的一次性提示，以及可選的「有新版本」通知與升級指令。"
      ],
      "zh-CN": [
        "停止按钮 — 可中止生成中的回答；已生成的部分内容会保留并标记为“已停止”。",
        "更新信息 — 此更新日志、更新后的一次性提示，以及可选的“有新版本”通知与升级命令。"
      ],
      "ja": [
        "停止ボタン — 生成中の回答を中断できます。途中までの内容は保持され「停止しました」と表示されます。",
        "更新情報 — この変更履歴、更新後の一度きりの通知、そして任意の「アップデートあり」通知とアップグレードコマンド。"
      ]
    }
  },
  {
    "version": "2.36.0",
    "date": "2026-06-16",
    "highlights": {
      "en": [
        "Slide source grounding — every slide traces back to the paper sections it was written from, shown in a Sources strip that opens the Citation Canvas.",
        "Manual slide editing — edit a frame or the whole deck's LaTeX and recompile, with a deterministic per-slide citation editor."
      ],
      "zh-TW": [
        "投影片來源溯源 — 每張投影片都能追溯其依據的論文章節，並在「來源」列開啟引用畫布。",
        "手動編輯投影片 — 可編輯單張或整份投影片的 LaTeX 並重新編譯，並提供逐張的引用編輯器。"
      ],
      "zh-CN": [
        "幻灯片来源溯源 — 每张幻灯片都能追溯其依据的论文章节，并在“来源”栏打开引用画布。",
        "手动编辑幻灯片 — 可编辑单张或整份幻灯片的 LaTeX 并重新编译，并提供逐张的引用编辑器。"
      ],
      "ja": [
        "スライドの出典トレース — 各スライドが依拠した論文セクションまで遡れ、Sources 列から引用キャンバスを開けます。",
        "スライドの手動編集 — フレーム単位またはデッキ全体の LaTeX を編集して再コンパイルでき、スライドごとの引用エディタも備えます。"
      ]
    }
  }
]
```

- [ ] **Step 3: Write the failing loader test**

Create `frontend/tests/lib/changelog.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { CHANGELOG, localizedHighlights, semverGt } from "@/lib/changelog";

describe("changelog loader", () => {
  it("exposes newest-first entries", () => {
    expect(CHANGELOG[0]!.version).toBe("2.37.0");
  });

  it("returns locale highlights, falling back to en", () => {
    const entry = CHANGELOG[0]!;
    expect(localizedHighlights(entry, "ja").length).toBeGreaterThan(0);
    // An unknown locale falls back to en.
    expect(localizedHighlights(entry, "fr")).toEqual(entry.highlights.en);
  });

  it("semverGt compares versions", () => {
    expect(semverGt("2.37.0", "2.36.0")).toBe(true);
    expect(semverGt("2.36.0", "2.37.0")).toBe(false);
    expect(semverGt("2.37.0", "2.37.0")).toBe(false);
  });
});
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/lib/changelog.test.ts`
Expected: FAIL — cannot resolve `@/lib/changelog`.

- [ ] **Step 5: Implement the loader**

Create `frontend/src/lib/changelog.ts`:

```typescript
import type { ChangelogEntry } from "@/types/domain";
import data from "@/changelog/changelog.json";

export const CHANGELOG: ChangelogEntry[] = data as ChangelogEntry[];

/** Highlights for a locale, falling back to `en` when the locale is absent. */
export function localizedHighlights(entry: ChangelogEntry, lng: string): string[] {
  return entry.highlights[lng] ?? entry.highlights.en ?? [];
}

/** True when semver `a` is strictly greater than `b` (major.minor.patch). */
export function semverGt(a: string, b: string): boolean {
  const pa = parse(a);
  const pb = parse(b);
  for (let i = 0; i < 3; i++) {
    if (pa[i]! > pb[i]!) return true;
    if (pa[i]! < pb[i]!) return false;
  }
  return false;
}

function parse(v: string): [number, number, number] {
  const parts = v.replace(/^v/, "").split(".").slice(0, 3);
  const n = parts.map((p) => parseInt(p.replace(/\D/g, ""), 10) || 0);
  return [n[0] ?? 0, n[1] ?? 0, n[2] ?? 0];
}
```

If `tsconfig`/Vite needs JSON module resolution, it is already enabled (the i18n catalogs import JSON). No config change expected.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/lib/changelog.test.ts`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/changelog/changelog.json frontend/src/lib/changelog.ts frontend/src/types/domain.ts frontend/tests/lib/changelog.test.ts
git commit -m "feat(about): bundled localized changelog data + loader (FR-16)"
```

---

### Task B4: Frontend — `getVersion()` API client

**Files:**
- Modify: `frontend/src/lib/api.ts`
- Test: `frontend/tests/lib/getVersion.test.ts`

- [ ] **Step 1: Write the failing test (MSW)**

Create `frontend/tests/lib/getVersion.test.ts`:

```typescript
import { afterEach, describe, expect, it } from "vitest";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { getVersion } from "@/lib/api";
import { API_BASE_URL } from "@/lib/api";

const server = setupServer(
  http.get(`${API_BASE_URL}/version`, () =>
    HttpResponse.json({
      current: "2.37.0",
      latest: "2.38.0",
      update_available: true,
      html_url: "https://github.com/whats2000/PaperHub/releases/tag/v2.38.0",
      checked_at: "2026-06-16T00:00:00Z",
    }),
  ),
);

server.listen();
afterEach(() => server.resetHandlers());

describe("getVersion", () => {
  it("fetches the version payload", async () => {
    const info = await getVersion();
    expect(info.current).toBe("2.37.0");
    expect(info.update_available).toBe(true);
  });
});
```

(If the repo already has a global MSW server in test setup, follow that pattern instead of a local `setupServer` — mirror an existing `tests/lib/*` test.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/lib/getVersion.test.ts`
Expected: FAIL — `getVersion` not exported.

- [ ] **Step 3: Add the client function + import the type**

In `frontend/src/lib/api.ts`, add `VersionInfo` to the type import block (top of file) and add:

```typescript
export async function getVersion(): Promise<VersionInfo> {
  return apiFetch<VersionInfo>(`/version`);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/lib/getVersion.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/api.ts frontend/tests/lib/getVersion.test.ts
git commit -m "feat(about): getVersion() API client (FR-16)"
```

---

### Task B5: Frontend — `about` i18n namespace

**Files:**
- Create: `frontend/src/locales/{en,zh-TW,zh-CN,ja}/about.json`
- Modify: `frontend/src/lib/i18n.ts`

- [ ] **Step 1: Create the four catalogs**

`frontend/src/locales/en/about.json`:

```json
{
  "title": "What's New",
  "currentVersion": "You're on v{{version}}",
  "updateAvailable": "Update available: v{{version}}",
  "updateHint": "Update with:",
  "copy": "Copy",
  "copied": "Copied",
  "viewRelease": "View release",
  "updatedToast": "Updated to v{{version}}",
  "whatsNewAction": "What's new",
  "close": "Close"
}
```

`frontend/src/locales/zh-TW/about.json`:

```json
{
  "title": "更新內容",
  "currentVersion": "目前版本 v{{version}}",
  "updateAvailable": "有新版本可用：v{{version}}",
  "updateHint": "升級指令：",
  "copy": "複製",
  "copied": "已複製",
  "viewRelease": "查看發行版本",
  "updatedToast": "已更新至 v{{version}}",
  "whatsNewAction": "查看更新",
  "close": "關閉"
}
```

`frontend/src/locales/zh-CN/about.json`:

```json
{
  "title": "更新内容",
  "currentVersion": "当前版本 v{{version}}",
  "updateAvailable": "有新版本可用：v{{version}}",
  "updateHint": "升级命令：",
  "copy": "复制",
  "copied": "已复制",
  "viewRelease": "查看发行版本",
  "updatedToast": "已更新至 v{{version}}",
  "whatsNewAction": "查看更新",
  "close": "关闭"
}
```

`frontend/src/locales/ja/about.json`:

```json
{
  "title": "新着情報",
  "currentVersion": "現在のバージョン v{{version}}",
  "updateAvailable": "アップデートがあります：v{{version}}",
  "updateHint": "アップグレードコマンド：",
  "copy": "コピー",
  "copied": "コピーしました",
  "viewRelease": "リリースを見る",
  "updatedToast": "v{{version}} に更新しました",
  "whatsNewAction": "新着情報",
  "close": "閉じる"
}
```

- [ ] **Step 2: Register the namespace in i18n.ts**

In `frontend/src/lib/i18n.ts`: add four imports (mirroring the existing per-locale import style), e.g. `import enAbout from "../locales/en/about.json";` (+ zh-TW, zh-CN, ja); add `about: enAbout,` (and the locale equivalents) to each locale in `resources`; and add `"about"` to the `ns: [...]` array.

- [ ] **Step 3: Run the parity test to verify**

Run: `cd frontend; npm test -- --run src/locales/parity.test.ts`
Expected: PASS (every locale has the same `about` keys).

- [ ] **Step 4: Commit**

```bash
git add frontend/src/locales frontend/src/lib/i18n.ts
git commit -m "feat(about): add the about i18n namespace across four locales (FR-16)"
```

---

### Task B6: Frontend — version store

**Files:**
- Create: `frontend/src/store/version.ts`
- Test: `frontend/tests/store/version.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/store/version.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";

const getVersionMock = vi.fn();
vi.mock("@/lib/api", () => ({ getVersion: () => getVersionMock() }));

import { useVersionStore } from "@/store/version";

afterEach(() => {
  getVersionMock.mockReset();
  useVersionStore.setState({ info: null, changelogOpen: false });
});

describe("version store", () => {
  it("fetchVersion stores the payload", async () => {
    getVersionMock.mockResolvedValue({
      current: "2.37.0",
      latest: "2.38.0",
      update_available: true,
      html_url: "x",
      checked_at: "y",
    });
    await useVersionStore.getState().fetchVersion();
    expect(useVersionStore.getState().info?.update_available).toBe(true);
  });

  it("fetchVersion swallows errors (info stays null)", async () => {
    getVersionMock.mockRejectedValue(new Error("offline"));
    await useVersionStore.getState().fetchVersion();
    expect(useVersionStore.getState().info).toBeNull();
  });

  it("open/close toggles the changelog modal", () => {
    useVersionStore.getState().openChangelog();
    expect(useVersionStore.getState().changelogOpen).toBe(true);
    useVersionStore.getState().closeChangelog();
    expect(useVersionStore.getState().changelogOpen).toBe(false);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/store/version.test.ts`
Expected: FAIL — cannot resolve `@/store/version`.

- [ ] **Step 3: Implement the store**

Create `frontend/src/store/version.ts`:

```typescript
import { create } from "zustand";
import type { VersionInfo } from "@/types/domain";
import { getVersion } from "@/lib/api";

interface VersionState {
  info: VersionInfo | null;
  changelogOpen: boolean;
  /** Fetch /version once; swallows errors so a missing/offline backend never
   *  surfaces a toast or breaks the menu. */
  fetchVersion: () => Promise<void>;
  openChangelog: () => void;
  closeChangelog: () => void;
}

export const useVersionStore = create<VersionState>((set) => ({
  info: null,
  changelogOpen: false,
  fetchVersion: async () => {
    try {
      const info = await getVersion();
      set({ info });
    } catch {
      // Self-hosted: an unreachable backend or disabled check is normal.
    }
  },
  openChangelog: () => set({ changelogOpen: true }),
  closeChangelog: () => set({ changelogOpen: false }),
}));
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/store/version.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/store/version.ts frontend/tests/store/version.test.ts
git commit -m "feat(about): version store (info + changelog modal state) (FR-16)"
```

---

### Task B7: Frontend — version-announce toast hook

**Files:**
- Create: `frontend/src/hooks/useVersionAnnounce.ts`
- Test: `frontend/tests/hooks/useVersionAnnounce.test.ts`

- [ ] **Step 1: Write the failing test**

Create `frontend/tests/hooks/useVersionAnnounce.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";

const toastMessage = vi.fn();
vi.mock("sonner", () => ({ toast: { message: (...a: unknown[]) => toastMessage(...a) } }));

import { useVersionAnnounce } from "@/hooks/useVersionAnnounce";

const KEY = "paperhub-last-seen-version";

afterEach(() => {
  toastMessage.mockReset();
  localStorage.clear();
});

describe("useVersionAnnounce", () => {
  it("does not toast on a first-ever load (sets lastSeen silently)", () => {
    renderHook(() => useVersionAnnounce("2.37.0"));
    expect(toastMessage).not.toHaveBeenCalled();
    expect(localStorage.getItem(KEY)).toBe("2.37.0");
  });

  it("toasts when the running version is newer than lastSeen", () => {
    localStorage.setItem(KEY, "2.36.0");
    renderHook(() => useVersionAnnounce("2.37.0"));
    expect(toastMessage).toHaveBeenCalledOnce();
    expect(localStorage.getItem(KEY)).toBe("2.37.0");
  });

  it("does not toast when lastSeen equals the running version", () => {
    localStorage.setItem(KEY, "2.37.0");
    renderHook(() => useVersionAnnounce("2.37.0"));
    expect(toastMessage).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/hooks/useVersionAnnounce.test.ts`
Expected: FAIL — cannot resolve the hook.

- [ ] **Step 3: Implement the hook**

Create `frontend/src/hooks/useVersionAnnounce.ts`:

```typescript
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { semverGt } from "@/lib/changelog";
import { useVersionStore } from "@/store/version";

const LAST_SEEN_KEY = "paperhub-last-seen-version";

/** Fires a one-time "you just updated" toast when the running build version is
 *  newer than the last version this browser saw (FR-16). A first-ever load sets
 *  the baseline silently (no toast on fresh installs). `runningVersion`
 *  defaults to the build-time __APP_VERSION__. */
export function useVersionAnnounce(runningVersion: string = __APP_VERSION__): void {
  const { t } = useTranslation("about");
  useEffect(() => {
    const lastSeen = localStorage.getItem(LAST_SEEN_KEY);
    if (lastSeen === null) {
      localStorage.setItem(LAST_SEEN_KEY, runningVersion);
      return;
    }
    if (semverGt(runningVersion, lastSeen)) {
      toast.message(t("updatedToast", { version: runningVersion }), {
        action: {
          label: t("whatsNewAction"),
          onClick: () => useVersionStore.getState().openChangelog(),
        },
      });
      localStorage.setItem(LAST_SEEN_KEY, runningVersion);
    }
  }, [runningVersion, t]);
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/hooks/useVersionAnnounce.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useVersionAnnounce.ts frontend/tests/hooks/useVersionAnnounce.test.ts
git commit -m "feat(about): one-time version-announce toast hook (FR-16)"
```

---

### Task B8: Frontend — ChangelogModal

**Files:**
- Create: `frontend/src/components/about/ChangelogModal.tsx`
- Test: `frontend/tests/components/ChangelogModal.test.tsx`

- [ ] **Step 1: Inspect the existing Dialog usage**

Open `frontend/src/components/settings/SettingsModal.tsx` to copy the exact Base-UI `Dialog` import + open/close wiring conventions this codebase uses (backdrop, positioner, popup classes). The ChangelogModal mirrors that shell.

- [ ] **Step 2: Write the failing test**

Create `frontend/tests/components/ChangelogModal.test.tsx`:

```typescript
import { afterEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ChangelogModal } from "@/components/about/ChangelogModal";
import { useVersionStore } from "@/store/version";

afterEach(() => {
  useVersionStore.setState({ info: null, changelogOpen: false });
});

describe("ChangelogModal", () => {
  it("renders entries when open", () => {
    useVersionStore.setState({
      changelogOpen: true,
      info: { current: "2.37.0", latest: null, update_available: false, html_url: null, checked_at: null },
    });
    render(<ChangelogModal />);
    expect(screen.getByText(/what's new/i)).toBeInTheDocument();
    expect(screen.getByText(/2\.37\.0/)).toBeInTheDocument();
  });

  it("shows the update-available row + command when an update exists", () => {
    useVersionStore.setState({
      changelogOpen: true,
      info: {
        current: "2.37.0",
        latest: "2.38.0",
        update_available: true,
        html_url: "https://github.com/whats2000/PaperHub/releases/tag/v2.38.0",
        checked_at: "2026-06-16T00:00:00Z",
      },
    });
    render(<ChangelogModal />);
    expect(screen.getByText(/update available/i)).toBeInTheDocument();
    expect(screen.getByText(/docker compose pull/i)).toBeInTheDocument();
  });

  it("renders nothing when closed", () => {
    useVersionStore.setState({ changelogOpen: false });
    const { container } = render(<ChangelogModal />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/components/ChangelogModal.test.tsx`
Expected: FAIL — cannot resolve the component.

- [ ] **Step 4: Implement the modal**

Create `frontend/src/components/about/ChangelogModal.tsx`. Use the same Base-UI `Dialog` primitives/classes as `SettingsModal.tsx` for the shell; the body is below. (Adapt the Dialog wrapper to match SettingsModal exactly; the content is what matters here.)

```tsx
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check, ExternalLink } from "lucide-react";

import { useVersionStore } from "@/store/version";
import { CHANGELOG, localizedHighlights } from "@/lib/changelog";

const UPDATE_COMMAND = "docker compose pull && docker compose up -d";

export function ChangelogModal() {
  const { t, i18n } = useTranslation("about");
  const open = useVersionStore((s) => s.changelogOpen);
  const close = useVersionStore((s) => s.closeChangelog);
  const info = useVersionStore((s) => s.info);
  const [copied, setCopied] = useState(false);

  if (!open) return null;

  const copy = () => {
    void navigator.clipboard.writeText(UPDATE_COMMAND);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={t("title")}
      onClick={close}
    >
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <button
            type="button"
            onClick={close}
            className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-accent"
          >
            {t("close")}
          </button>
        </div>

        {info && (
          <p className="mb-3 text-xs text-muted-foreground">
            {t("currentVersion", { version: info.current })}
          </p>
        )}

        {info?.update_available && info.latest && (
          <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
            <p className="font-medium text-amber-800 dark:text-amber-200">
              {t("updateAvailable", { version: info.latest })}
            </p>
            <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">{t("updateHint")}</p>
            <div className="mt-1 flex items-center gap-2">
              <code className="flex-1 truncate rounded bg-background px-2 py-1 text-xs">
                {UPDATE_COMMAND}
              </code>
              <button
                type="button"
                onClick={copy}
                aria-label={t("copy")}
                className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs hover:bg-accent"
              >
                {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                {copied ? t("copied") : t("copy")}
              </button>
            </div>
            {info.html_url && (
              <a
                href={info.html_url}
                target="_blank"
                rel="noreferrer"
                className="mt-2 inline-flex items-center gap-1 text-xs underline"
              >
                <ExternalLink className="h-3 w-3" />
                {t("viewRelease")}
              </a>
            )}
          </div>
        )}

        <ul className="space-y-4">
          {CHANGELOG.map((entry) => (
            <li key={entry.version}>
              <div className="flex items-baseline justify-between">
                <span className="text-sm font-semibold">v{entry.version}</span>
                <span className="text-xs text-muted-foreground">{entry.date}</span>
              </div>
              <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-foreground/90">
                {localizedHighlights(entry, i18n.language).map((h, i) => (
                  <li key={i}>{h}</li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/components/ChangelogModal.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/about/ChangelogModal.tsx frontend/tests/components/ChangelogModal.test.tsx
git commit -m "feat(about): ChangelogModal with update-available row (FR-16)"
```

---

### Task B9: Frontend — wire AccountMenu + App root

**Files:**
- Modify: `frontend/src/components/layout/AccountMenu.tsx`
- Modify: `frontend/src/App.tsx`
- Test: `frontend/tests/components/AccountMenuAbout.test.tsx`

- [ ] **Step 1: Write the failing AccountMenu test**

Create `frontend/tests/components/AccountMenuAbout.test.tsx`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AccountMenu } from "@/components/layout/AccountMenu";
import { useVersionStore } from "@/store/version";

vi.mock("next-themes", () => ({ useTheme: () => ({ theme: "light", setTheme: vi.fn() }) }));

afterEach(() => useVersionStore.setState({ info: null, changelogOpen: false }));

describe("AccountMenu — About opens changelog", () => {
  it("clicking About opens the changelog modal", async () => {
    render(<AccountMenu collapsed={false} onOpenSettings={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: /account/i }));
    await userEvent.click(screen.getByText(/about/i));
    expect(useVersionStore.getState().changelogOpen).toBe(true);
  });
});
```

(If opening the Base-UI menu in jsdom needs a different trigger query, mirror an existing AccountMenu test under `frontend/tests/`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/components/AccountMenuAbout.test.tsx`
Expected: FAIL — About is a disabled, non-interactive item.

- [ ] **Step 3: Make the About line a button + add the update dot**

In `AccountMenu.tsx`:

Add the store import:

```typescript
import { useVersionStore } from "@/store/version";
```

Inside the component, read the store:

```typescript
  const openChangelog = useVersionStore((s) => s.openChangelog);
  const updateAvailable = useVersionStore((s) => s.info?.update_available ?? false);
```

Replace the disabled About `Menu.Item` (~lines 111-113) with a clickable one:

```tsx
            <Menu.Item className={`${ITEM_CLASS} gap-2`} onClick={openChangelog}>
              {t("about")} · v{APP_VERSION}
            </Menu.Item>
```

Add an update dot to the trigger avatar (so it shows even when the menu is closed). Inside `<Menu.Trigger>`, wrap the avatar `<span>` so the dot overlays it:

```tsx
        <span className="relative grid size-7 shrink-0 place-items-center rounded-full bg-muted">
          <User className="size-4" />
          {updateAvailable && (
            <span
              className="absolute -right-0.5 -top-0.5 size-2 rounded-full bg-amber-500"
              aria-label={t("updateBadge")}
            />
          )}
        </span>
```

Add `"updateBadge": "Update available"` (en) to `frontend/src/locales/en/common.json`, plus the three translations (`zh-TW`/`zh-CN`: `"有可用更新"`; `ja`: `"アップデートあり"`).

- [ ] **Step 4: Mount the modal + fetch version + announce at the app root**

In `frontend/src/App.tsx`: import `useEffect`, the store, the hook, and the modal; then inside the root component add (near the top of the returned tree / a top-level effect):

```tsx
import { useEffect } from "react";
import { ChangelogModal } from "@/components/about/ChangelogModal";
import { useVersionAnnounce } from "@/hooks/useVersionAnnounce";
import { useVersionStore } from "@/store/version";
```

```tsx
  useVersionAnnounce();
  useEffect(() => {
    void useVersionStore.getState().fetchVersion();
  }, []);
```

and render `<ChangelogModal />` once at the top level of the app's JSX (a sibling of the router/layout, so it overlays everything). Place the `useVersionAnnounce()` + `useEffect` inside the existing root function component (alongside other hooks); follow App.tsx's current structure.

- [ ] **Step 5: Run the tests + parity**

Run: `cd frontend; npm test -- --run tests/components/AccountMenuAbout.test.tsx src/locales/parity.test.ts`
Expected: PASS.

- [ ] **Step 6: typecheck/lint + commit**

Run: `cd frontend; npm run typecheck; npm run lint`
Expected: clean.

```bash
git add frontend/src/components/layout/AccountMenu.tsx frontend/src/App.tsx frontend/src/locales frontend/tests/components/AccountMenuAbout.test.tsx
git commit -m "feat(about): About opens changelog + update dot, fetch+announce at root (FR-16)"
```

---

### Task B10: Document the changelog step in merge-prep

**Files:**
- Modify: `.claude/skills/paperhub-merge-prep/SKILL.md`

- [ ] **Step 1: Add a changelog-entry sub-step**

In `.claude/skills/paperhub-merge-prep/SKILL.md`, in **§4 — Update the SRS** (or as a new sibling step right after it), add:

```markdown
## 4b — Prepend the in-app changelog entry (FR-16)

`frontend/src/changelog/changelog.json` is the user-facing changelog the
`ChangelogModal` renders. Prepend ONE new entry (newest-first) for this
version:

- `version` = the new X.Y.Z (no `v`), `date` = today (YYYY-MM-DD).
- `highlights` = 1–3 SHORT, user-facing bullets per locale (`en`, `zh-TW`,
  `zh-CN`, `ja`) — what the user can now DO, not the dense SRS prose. `en` is
  source-of-truth; translate the others (en-fallback is tolerated but prefer
  real translations to match the i18n posture).

This is distinct from the SRS Revision-History row (internal/engineering).
Stage `frontend/src/changelog/changelog.json` with the release commit in §5.
```

Also add `frontend/src/changelog/changelog.json` to the list of files staged in **§5** (the "typically twelve files" enumeration becomes thirteen).

- [ ] **Step 2: Commit**

```bash
git add .claude/skills/paperhub-merge-prep/SKILL.md
git commit -m "docs(merge-prep): add the in-app changelog-entry step (FR-16)"
```

---

## Plan-phase completion gates (run once, after all tasks)

- [ ] **Backend full suite + lint + types**

Run: `cd backend; uv run pytest; uv run ruff check src tests; uv run mypy src`
Expected: all green; "All checks passed!"; "Success: no issues found".

- [ ] **Frontend full suite + types + lint + build**

Run: `cd frontend; npm test -- --run; npm run typecheck; npm run lint; npm run build`
Expected: all green; production build succeeds.

- [ ] **Real-API gate (per CLAUDE.md — at plan-phase completion, on the user's live `:8000`)**

1. Confirm `:8000` is live (`curl -s -m 3 http://127.0.0.1:8000/health`); if not, ASK the user to start it — do not boot your own.
2. **FR-16:** `curl -s http://127.0.0.1:8000/version` → confirm `{current, latest, update_available, ...}`; toggle `PAPERHUB_UPDATE_CHECK=0` via Settings → `latest` becomes `null`.
3. **FR-15:** `POST /sessions` → `POST /chat` with a long-running prompt (e.g. a `slides` or `paper_qa` turn), then drop the connection mid-stream; query `SELECT status FROM runs ORDER BY id DESC LIMIT 1` → expect `cancelled` (not `running`/`error`); confirm a partial assistant message row exists if tokens streamed.
4. ASK the user to confirm visually in the frontend: the Stop button appears while streaming and leaves a "Stopped" partial; the "What's New" modal opens from the account menu; the update dot shows when a newer release exists.

---

## Self-Review

**Spec coverage (FR-15):** Stop button (A4) ✓; client abort → cancel without error (A3) ✓; `runs.status='cancelled'` + partial persist + re-raise (A1) ✓; "Stopped" marker, not error card (A2 status + A5 render) ✓; distinguish deliberate Stop from implicit abort-on-new-send via `userStoppedRef` (A3) ✓; no new endpoint (A1 uses the disconnect path) ✓; FR-09 cross-ref made true (A1) ✓.

**Spec coverage (FR-16):** bundled localized `changelog.json` outside the t() namespaces (B3) ✓; `about` chrome namespace × 4 locales (B5) ✓; `GET /version` cached GitHub check, failure-swallowing, repo-slug env override, toggle-gated default-on (B1+B2) ✓; nginx proxy (B2) ✓; About→`ChangelogModal` + update dot (B8+B9) ✓; one-time announce toast w/ localStorage `lastSeen`, silent first-load (B7) ✓; `update_available` row w/ copy-paste command + release link, no in-app execution (B8) ✓; merge-prep step (B10) ✓.

**Placeholder scan:** every code step contains complete code; the two "inspect the existing file" steps (A5 MessageBubble, B8 Dialog shell) are read-then-integrate with the exact snippet + i18n key provided — not placeholders.

**Type consistency:** `cancelMessage(sessionId, run_id)` / `cancelPendingAssistant(sessionId)` consistent across A2↔A3; `VersionInfo` fields identical across domain.ts (B3), api (B4), store (B6), endpoint (B2); `semverGt`/`localizedHighlights`/`CHANGELOG` consistent across B3↔B7↔B8; `useVersionStore` shape `{info, changelogOpen, fetchVersion, openChangelog, closeChangelog}` consistent across B6↔B7↔B8↔B9; `composer.stop`/`composer.stopped` keys consistent across A4↔A5.

**Known integration notes (not gaps):** A1 Step 5 enumerates 6 token-yield sites by branch — the implementer appends one line at each; the test exercises the chitchat branch. A5/B8/B9 require reading sibling files for exact JSX shell conventions (MessageBubble status block, SettingsModal Dialog, App.tsx root) — the substantive code is specified.
