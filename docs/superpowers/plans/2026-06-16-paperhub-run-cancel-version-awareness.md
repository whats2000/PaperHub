# Run Cancellation + Version/Changelog Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v2.37.0 — (A) a Stop button that cleanly cancels an in-flight chat turn (client abort → backend finalizes `runs.status='cancelled'`), and (B) a localized in-app changelog + "you just updated" toast + an optional GitHub update-available check.

**Architecture:** Two independent features touching disjoint code except shared i18n + the SRS. (A) reuses the already-present `AbortController` in `useChatStream`; `sse-starlette` already cancels the stream generator on disconnect, so the only backend change is catching `asyncio.CancelledError` to finalize the run + persist partial text. (B) ships a bundled `changelog.json` (localized, en-fallback), a `GET /version` endpoint that does a cached GitHub latest-release lookup gated by a settings toggle, a `ChangelogModal`, and a one-time announce toast.

**Tech Stack:** Backend — FastAPI, aiosqlite, httpx, `importlib.metadata`. Frontend — React 19 + TS strict, Zustand, react-i18next, Sonner, Base-UI Dialog, lucide-react. Tests — pytest (backend), Vitest + RTL + MSW (frontend).

**Spec:** SRS FR-15 (run cancellation) + FR-16 (version/changelog/update awareness), `docs/superpowers/specs/2026-05-17-paperhub-srs.md`.

**Per-task gates (CLAUDE.md):** backend from `backend/` via `uv run`; frontend from `frontend/` via `npm`. Run only the touched test files + targeted `ruff`/`mypy`/`typecheck`/`lint` per task; full suites at plan-phase completion. Conventional Commits; focused per-concern commits; never stage build output.

---

## File Structure

**Part A — Run cancellation:**
- `backend/src/paperhub/api/chat.py` (modify) — `_finalise_cancelled` helper; `partial_chunks` accumulator at each token yield; `except asyncio.CancelledError` handler.
- `backend/tests/test_chat_cancel.py` (create) — unit + generator-athrow integration tests.
- `frontend/src/types/domain.ts` (modify) — `ChatMessage.status` gains `"cancelled"`.
- `frontend/src/store/chat.ts` (modify) — `cancelMessage` + `cancelPendingAssistant` actions.
- `frontend/src/hooks/useChatStream.ts` (modify) — `stop()` + `userStoppedRef`; abort → cancel, no throw.
- `frontend/src/components/chat/Composer.tsx` (modify) — Stop button while streaming.
- `frontend/src/components/chat/MessageBubble.tsx` (modify) — render `"cancelled"` status.
- `frontend/src/pages/ChatPage.tsx` (modify) — wire `isStreaming` + `onStop`.
- `frontend/src/locales/{en,zh-TW,zh-CN,ja}/chat.json` (modify) — `composer.stop`, `composer.stopped`.

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

### Task A1: Backend — finalize a cancelled run + persist partial text

**Files:**
- Modify: `backend/src/paperhub/api/chat.py`
- Test: `backend/tests/test_chat_cancel.py`

- [ ] **Step 1: Write the failing unit test for `_finalise_cancelled`**

Create `backend/tests/test_chat_cancel.py`:

```python
import asyncio

import aiosqlite

from paperhub.api.chat import _finalise_cancelled, _new_run
from paperhub.config import load_settings
from paperhub.db.migrate import apply_schema


async def _seed_session(conn: aiosqlite.Connection) -> int:
    await conn.execute("INSERT INTO chat_sessions DEFAULT VALUES")
    await conn.commit()
    async with conn.execute("SELECT last_insert_rowid()") as cur:
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_finalise_cancelled_sets_status_and_persists_partial(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        run_id = await _new_run(conn, session_id)

        await _finalise_cancelled(conn, run_id, session_id, "partial answer so far")

        async with conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)) as cur:
            status_row = await cur.fetchone()
        assert status_row is not None and status_row[0] == "cancelled"
        async with conn.execute(
            "SELECT content FROM messages WHERE run_id = ? AND role = 'assistant'", (run_id,)
        ) as cur:
            msg_row = await cur.fetchone()
        assert msg_row is not None and msg_row[0] == "partial answer so far"


async def test_finalise_cancelled_skips_empty_partial(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)
        session_id = await _seed_session(conn)
        run_id = await _new_run(conn, session_id)

        await _finalise_cancelled(conn, run_id, session_id, "")

        async with conn.execute("SELECT status FROM runs WHERE id = ?", (run_id,)) as cur:
            status_row = await cur.fetchone()
        assert status_row is not None and status_row[0] == "cancelled"
        async with conn.execute(
            "SELECT COUNT(*) FROM messages WHERE run_id = ? AND role = 'assistant'", (run_id,)
        ) as cur:
            count_row = await cur.fetchone()
        assert count_row is not None and count_row[0] == 0  # empty partial → no message
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd backend; uv run pytest tests/test_chat_cancel.py -v`
Expected: FAIL — `ImportError: cannot import name '_finalise_cancelled'`.

- [ ] **Step 3: Add the `_finalise_cancelled` helper**

In `backend/src/paperhub/api/chat.py`, immediately after the existing `_finalise` function (ends ~line 164), add:

```python
async def _finalise_cancelled(
    conn: aiosqlite.Connection,
    run_id: int,
    session_id: int,
    partial_content: str,
) -> None:
    """Finalize a run cancelled by client disconnect (FR-15). Sets
    ``runs.status='cancelled'`` and persists the partial assistant text only
    when non-empty, so a Stop that lands before any token leaves no empty
    bubble in cross-device replay (v2.15)."""
    if partial_content:
        await conn.execute(
            "INSERT INTO messages (session_id, role, content, run_id) "
            "VALUES (?, 'assistant', ?, ?)",
            (session_id, partial_content, run_id),
        )
    await conn.execute(
        "UPDATE runs SET finished_at = datetime('now'), status = 'cancelled' "
        "WHERE id = ?",
        (run_id,),
    )
    await conn.commit()
```

- [ ] **Step 4: Run the unit tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_chat_cancel.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Add the partial-text accumulator + the CancelledError handler**

In `chat.py`, ensure the module imports `asyncio` and `contextlib` (add either if missing near the top imports).

In `stream_events()`, declare the accumulator right after `last_emitted_step = -1` (~line 600):

```python
            last_emitted_step = -1
            # Accumulate streamed tokens so a mid-stream cancel (FR-15) can
            # persist the partial answer. Each token-yield site appends here.
            partial_chunks: list[str] = []
```

At **every** `yield {"event": "token", ...}` site inside `stream_events` (the branches: chitchat ~654, clarify ~665, paper_qa ~785, library_stats/sql ~863, memory ~877, intercept ~903), add an append of the same text passed to `TokenEvent(... text=X)` immediately BEFORE the yield. Example for the chitchat branch:

```python
                        token_evt = TokenEvent(run_id=run_id, branch="", text=token)
                        partial_chunks.append(token)
                        yield {"event": "token",
                               "data": token_evt.model_dump_json(exclude={"type"})}
```

Apply the identical one-line `partial_chunks.append(<text var>)` before each of the other token yields (`final_content`, `item`, etc. — append whatever variable that site passes as `text=`).

Then add the cancellation handler BEFORE the existing `except Exception` block (~line 950). Order matters: `CancelledError` is a `BaseException`, so `except Exception` never catches it — the new handler must come first:

```python
            except asyncio.CancelledError:
                # Client disconnected (Stop / navigation). sse-starlette cancels
                # this generator task; persist what streamed + flip the run to
                # 'cancelled' (closing the FR-09 stuck-'running' gap), then
                # re-raise so sse-starlette completes teardown. Shielded so the
                # DB write survives the in-flight cancellation.
                with contextlib.suppress(Exception):
                    await asyncio.shield(
                        _finalise_cancelled(
                            conn, run_id, session_id, "".join(partial_chunks),
                        )
                    )
                raise
            except Exception as exc:
```

- [ ] **Step 6: Write the generator-level integration test**

Append to `backend/tests/test_chat_cancel.py`:

```python
import aiosqlite as _aiosqlite  # noqa: E402  (re-alias for clarity below)

from starlette.requests import Request

from paperhub.api.chat import chat_endpoint
from paperhub.app import create_app
from paperhub.models.chat import ChatRequest


def _fake_request(app) -> Request:
    return Request({"type": "http", "method": "POST", "headers": [], "app": app})


async def test_chat_stream_cancel_marks_run_cancelled(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PAPERHUB_WORKSPACE", str(tmp_path))
    monkeypatch.setenv(
        "PAPERHUB_ROUTER_MOCK",
        '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"hi"}',
    )
    monkeypatch.setenv("PAPERHUB_CHITCHAT_MOCK", "Hello there, here is a partial answer")
    settings = load_settings()
    async with aiosqlite.connect(settings.db_path) as conn:
        await apply_schema(conn)

    app = create_app()
    req = ChatRequest(session_id=None, user_message="hi", history=[])
    resp = await chat_endpoint(req, _fake_request(app))
    gen = resp.body_iterator

    # Consume the first couple of events (session + at least one token), then
    # simulate the sse-starlette disconnect cancellation.
    await gen.__anext__()  # session event
    await gen.__anext__()  # routing/token (enough to start the turn)
    try:
        await gen.athrow(asyncio.CancelledError())
    except asyncio.CancelledError:
        pass  # re-raised by the handler, as designed

    async with aiosqlite.connect(settings.db_path) as conn:
        async with conn.execute("SELECT status FROM runs ORDER BY id DESC LIMIT 1") as cur:
            row = await cur.fetchone()
    assert row is not None and row[0] == "cancelled"
```

If the chitchat path dereferences `request.app.state.mcp_registry`, set `app.state.mcp_registry = None` is insufficient — instead reuse the helper from `tests/test_chat_sse.py` (`_wire_test_app`) by importing it; the chitchat branch (router + chitchat mock) does not dispatch MCP, so a bare `create_app()` is expected to suffice.

- [ ] **Step 7: Run the cancel tests to verify they pass**

Run: `cd backend; uv run pytest tests/test_chat_cancel.py -v`
Expected: PASS (3 passed).

- [ ] **Step 8: Targeted gates**

Run: `cd backend; uv run ruff check src tests; uv run mypy src`
Expected: "All checks passed!" and "Success: no issues found".

- [ ] **Step 9: Commit**

```bash
git add backend/src/paperhub/api/chat.py backend/tests/test_chat_cancel.py
git commit -m "feat(chat): finalize cancelled runs and persist partial text (FR-15)"
```

---

### Task A2: Frontend store — `cancelled` status + cancel actions

**Files:**
- Modify: `frontend/src/types/domain.ts:182` (the `ChatMessage.status` union)
- Modify: `frontend/src/store/chat.ts`
- Test: `frontend/tests/store/chatCancel.test.ts`

- [ ] **Step 1: Write the failing store test**

Create `frontend/tests/store/chatCancel.test.ts`:

```typescript
import { beforeEach, describe, expect, it } from "vitest";
import { useChatStore } from "@/store/chat";

describe("chat store — cancel actions", () => {
  beforeEach(() => {
    useChatStore.getState().reset();
  });

  it("cancelMessage keeps partial content and sets status 'cancelled'", () => {
    const s = useChatStore.getState();
    const sid = s.newSession();
    s.appendMessage(sid, { role: "user", content: "hi", run_id: null });
    s.appendMessage(sid, { role: "assistant", content: "", run_id: 7, status: "streaming" });
    s.appendToken(sid, 7, "partial so far");

    s.cancelMessage(sid, 7);

    const msg = useChatStore
      .getState()
      .sessions.find((x) => x.id === sid)!
      .messages.find((m) => m.run_id === 7)!;
    expect(msg.status).toBe("cancelled");
    expect(msg.content).toBe("partial so far");
  });

  it("cancelPendingAssistant marks the last streaming assistant cancelled", () => {
    const s = useChatStore.getState();
    const sid = s.newSession();
    s.appendMessage(sid, { role: "assistant", content: "", run_id: null, status: "streaming" });

    s.cancelPendingAssistant(sid);

    const msgs = useChatStore.getState().sessions.find((x) => x.id === sid)!.messages;
    expect(msgs[msgs.length - 1]!.status).toBe("cancelled");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/store/chatCancel.test.ts`
Expected: FAIL — `cancelMessage is not a function`.

- [ ] **Step 3: Extend the `ChatMessage.status` union**

In `frontend/src/types/domain.ts`, change the `ChatMessage.status` field:

```typescript
  status?: "streaming" | "ok" | "error" | "cancelled";
```

- [ ] **Step 4: Add the two actions to the store interface + implementation**

In `frontend/src/store/chat.ts`, add to the `ChatState` interface (next to `errorMessage`):

```typescript
  cancelMessage: (sessionId: number, run_id: number) => void;
  cancelPendingAssistant: (sessionId: number) => void;
```

And add the implementations after `errorMessage` (~line 298):

```typescript
      cancelMessage: (sessionId, run_id) =>
        set((s) => {
          const msg = s.sessions
            .find((x) => x.id === sessionId)
            ?.messages.find((m) => m.run_id === run_id && m.role === "assistant");
          const trace = (msg?.trace ?? []).filter((r) => r.step_index >= 0);
          return {
            sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
              status: "cancelled",
              trace,
            }),
          };
        }),

      cancelPendingAssistant: (sessionId) =>
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === sessionId
              ? {
                  ...sess,
                  messages: sess.messages.map((m, i, arr) =>
                    i === arr.length - 1 &&
                    m.role === "assistant" &&
                    (m.status === "streaming" || m.status === undefined)
                      ? { ...m, status: "cancelled" }
                      : m,
                  ),
                }
              : sess,
          ),
        })),
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/store/chatCancel.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types/domain.ts frontend/src/store/chat.ts frontend/tests/store/chatCancel.test.ts
git commit -m "feat(chat): add cancelled message state + cancel store actions (FR-15)"
```

---

### Task A3: Frontend hook — `stop()` + treat user abort as cancel

**Files:**
- Modify: `frontend/src/hooks/useChatStream.ts`
- Test: `frontend/tests/hooks/useChatStreamStop.test.ts`

- [ ] **Step 1: Write the failing hook test**

Create `frontend/tests/hooks/useChatStreamStop.test.ts`:

```typescript
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";

// Mock the SSE layer so we control when/how the stream rejects.
const streamChatMock = vi.fn();
vi.mock("@/lib/sse", () => ({ streamChat: (...args: unknown[]) => streamChatMock(...args) }));
vi.mock("@/lib/api", () => ({ listSessionReferences: vi.fn().mockResolvedValue([]) }));

import { useChatStream } from "@/hooks/useChatStream";
import { useChatStore } from "@/store/chat";

afterEach(() => {
  streamChatMock.mockReset();
  useChatStore.getState().reset();
});

it("stop() aborts and finalizes the streaming message as cancelled (no throw)", async () => {
  const sid = useChatStore.getState().newSession();
  useChatStore.getState().patchSessionBackendId(sid, 100);

  // streamChat: emit a session event (gives a run_id), then a token, then hang
  // until aborted — reject with an AbortError when the signal fires.
  streamChatMock.mockImplementation((_body, handlers, signal: AbortSignal) => {
    handlers.onEvent("session", { run_id: 5, session_id: 100 });
    handlers.onEvent("token", { run_id: 5, branch: "", text: "partial" });
    return new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () =>
        reject(new DOMException("Aborted", "AbortError")),
      );
    });
  });

  const { result } = renderHook(() => useChatStream());
  let sendPromise!: Promise<void>;
  act(() => {
    sendPromise = result.current.send(sid, "hello");
  });
  // Let the synchronous onEvent calls run.
  await Promise.resolve();
  act(() => result.current.stop());
  await expect(sendPromise).resolves.toBeUndefined(); // does NOT throw

  const msg = useChatStore
    .getState()
    .sessions.find((x) => x.id === sid)!
    .messages.find((m) => m.run_id === 5)!;
  expect(msg.status).toBe("cancelled");
  expect(msg.content).toBe("partial");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/hooks/useChatStreamStop.test.ts`
Expected: FAIL — `result.current.stop is not a function`.

- [ ] **Step 3: Add `stop()` + the userStopped flag, and handle abort**

In `frontend/src/hooks/useChatStream.ts`:

Add a ref next to `abortRef` (~line 25):

```typescript
  const abortRef = useRef<AbortController | null>(null);
  const userStoppedRef = useRef(false);
```

At the start of `send` (after `abortRef.current = new AbortController();`, ~line 30), reset the flag:

```typescript
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    userStoppedRef.current = false;
```

Replace the outer `catch (err)` block (~lines 168-181) with abort-aware handling:

```typescript
    } catch (err) {
      const aborted =
        userStoppedRef.current ||
        (err instanceof DOMException && err.name === "AbortError");
      if (aborted && userStoppedRef.current) {
        // Deliberate Stop (FR-15): finalize the partial answer as cancelled.
        // An implicit abort-on-new-send leaves userStoppedRef false and falls
        // through to the existing error path below.
        if (runId !== null) {
          store.getState().cancelMessage(sessionId, runId);
        } else {
          store.getState().cancelPendingAssistant(sessionId);
        }
        return; // swallow — not an error
      }
      // fetchEventSource may throw synchronously before onerror fires
      // (e.g. CORS preflight reject, immediate connection refused).
      if (!handledInline && runId === null) {
        const msg = err instanceof Error ? err.message : String(err);
        store.getState().failPendingAssistant(sessionId, msg);
      }
      if (!handledInline) {
        throw err;
      }
    }
```

Add `stop` to the returned API and wrap it in `useCallback`. Replace the `return { send };` (~line 184):

```typescript
  const stop = useCallback(() => {
    userStoppedRef.current = true;
    abortRef.current?.abort();
  }, []);

  return { send, stop };
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/hooks/useChatStreamStop.test.ts`
Expected: PASS.

- [ ] **Step 5: Targeted typecheck/lint**

Run: `cd frontend; npm run typecheck; npm run lint`
Expected: no errors. (`tsc -b` prints errors but exits 0 — READ the output, don't trust the exit code.)

- [ ] **Step 6: Commit**

```bash
git add frontend/src/hooks/useChatStream.ts frontend/tests/hooks/useChatStreamStop.test.ts
git commit -m "feat(chat): useChatStream.stop() cancels the turn without erroring (FR-15)"
```

---

### Task A4: Frontend Composer — Stop button while streaming

**Files:**
- Modify: `frontend/src/components/chat/Composer.tsx`
- Test: `frontend/tests/components/ComposerStop.test.tsx`

- [ ] **Step 1: Write the failing component test**

Create `frontend/tests/components/ComposerStop.test.tsx`:

```typescript
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Composer } from "@/components/chat/Composer";

describe("Composer Stop button (FR-15)", () => {
  it("shows Stop while streaming and calls onStop", async () => {
    const onStop = vi.fn();
    render(<Composer onSubmit={vi.fn()} disabled isStreaming onStop={onStop} />);
    const stop = screen.getByRole("button", { name: /stop/i });
    await userEvent.click(stop);
    expect(onStop).toHaveBeenCalledOnce();
  });

  it("shows Send (not Stop) when idle", () => {
    render(<Composer onSubmit={vi.fn()} disabled={false} />);
    expect(screen.queryByRole("button", { name: /stop/i })).toBeNull();
    expect(screen.getByRole("button", { name: /send/i })).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/components/ComposerStop.test.tsx`
Expected: FAIL — no Stop button.

- [ ] **Step 3: Add the props + render the Stop button**

In `Composer.tsx`, import `Square` from lucide:

```typescript
import {
  BookOpen,
  BrainCircuit,
  Mic,
  Presentation,
  Columns2,
  Send,
  Square,
} from "lucide-react";
```

Add to `Props` (after `slideChip`):

```typescript
  /** True while a turn is streaming — swaps Send for a Stop control (FR-15). */
  isStreaming?: boolean;
  /** Cancels the in-flight turn (wired to useChatStream.stop). */
  onStop?: () => void;
```

Destructure them in the component signature (with defaults):

```typescript
  slideChip = null,
  isStreaming = false,
  onStop,
}: Props) {
```

Replace the submit `<Button type="submit" ...>` block (~lines 351-359) with a Stop/Send switch:

```typescript
                {isStreaming ? (
                  <Button
                    type="button"
                    size="icon"
                    onClick={onStop}
                    aria-label={t("composer.stop")}
                    className="h-8 w-8 rounded-full"
                  >
                    <Square className="h-4 w-4" />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    disabled={disabled || value.trim().length === 0}
                    aria-label={t("composer.send")}
                    className="h-8 w-8 rounded-full"
                  >
                    <Send className="h-4 w-4" />
                  </Button>
                )}
```

- [ ] **Step 4: Add the i18n keys (all four locales)**

In `frontend/src/locales/en/chat.json`, under `"composer"`, add:

```json
    "stop": "Stop",
    "stopped": "Stopped",
```

In `frontend/src/locales/zh-TW/chat.json` (composer): `"stop": "停止"`, `"stopped": "已停止"`.
In `frontend/src/locales/zh-CN/chat.json` (composer): `"stop": "停止"`, `"stopped": "已停止"`.
In `frontend/src/locales/ja/chat.json` (composer): `"stop": "停止"`, `"stopped": "停止しました"`.

- [ ] **Step 5: Run the test + parity to verify**

Run: `cd frontend; npm test -- --run tests/components/ComposerStop.test.tsx src/locales/parity.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/Composer.tsx frontend/src/locales frontend/tests/components/ComposerStop.test.tsx
git commit -m "feat(chat): Composer Stop button + i18n while streaming (FR-15)"
```

---

### Task A5: Frontend MessageBubble — render the cancelled state

**Files:**
- Modify: `frontend/src/components/chat/MessageBubble.tsx`
- Test: `frontend/tests/components/MessageBubbleCancelled.test.tsx`

- [ ] **Step 1: Read the existing status handling**

Open `frontend/src/components/chat/MessageBubble.tsx` and locate where `status === "error"` is handled and where the assistant `content` (markdown) is rendered for the `"ok"`/`"streaming"` case. The cancelled branch renders content identically to `ok`, then appends a muted "Stopped" chip.

- [ ] **Step 2: Write the failing test**

Create `frontend/tests/components/MessageBubbleCancelled.test.tsx`:

```typescript
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "@/components/chat/MessageBubble";
import type { ChatMessage } from "@/types/domain";

describe("MessageBubble — cancelled (FR-15)", () => {
  it("renders partial content plus a Stopped marker", () => {
    const msg: ChatMessage = {
      role: "assistant",
      content: "partial answer that was interrupted",
      run_id: 9,
      status: "cancelled",
    };
    render(<MessageBubble message={msg} />);
    expect(screen.getByText(/partial answer that was interrupted/)).toBeInTheDocument();
    expect(screen.getByText(/stopped/i)).toBeInTheDocument();
  });
});
```

(If `MessageBubble` requires more props than `message`, supply the minimal extras the existing tests in `frontend/tests/components/` already pass — mirror a sibling MessageBubble test.)

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd frontend; npm test -- --run tests/components/MessageBubbleCancelled.test.tsx`
Expected: FAIL — no "Stopped" text.

- [ ] **Step 4: Render the cancelled marker**

In `MessageBubble.tsx`, ensure the content body renders for `status === "cancelled"` exactly as for `"ok"` (extend any `status === "ok"` / non-error condition to also accept `"cancelled"`). Then, after the content body for an assistant message, add a muted marker shown only when cancelled. Use the `chat` namespace `t` already used in the file (or add `const { t } = useTranslation("chat");` if not present):

```tsx
{message.role === "assistant" && message.status === "cancelled" && (
  <div className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground">
    <Square className="h-3 w-3" />
    {t("composer.stopped")}
  </div>
)}
```

Import `Square` from `lucide-react` in this file if not already imported.

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd frontend; npm test -- --run tests/components/MessageBubbleCancelled.test.tsx`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/chat/MessageBubble.tsx frontend/tests/components/MessageBubbleCancelled.test.tsx
git commit -m "feat(chat): render the stopped state on cancelled messages (FR-15)"
```

---

### Task A6: Wire Stop into ChatPage

**Files:**
- Modify: `frontend/src/pages/ChatPage.tsx`

- [ ] **Step 1: Pull `stop` from the hook**

In `ChatPage.tsx`, change the hook destructure (~line 54):

```typescript
  const { send, stop } = useChatStream();
```

- [ ] **Step 2: Pass `isStreaming` + `onStop` to the Composer**

In the `<Composer ... />` usage (~line 264), add:

```tsx
        <Composer
          onSubmit={handleSubmit}
          disabled={isStreaming || setupRequired}
          isStreaming={isStreaming}
          onStop={stop}
          setupRequired={setupRequired}
```

(leave the remaining props unchanged.)

- [ ] **Step 3: Verify typecheck/lint + the broad frontend chat suite**

Run: `cd frontend; npm run typecheck; npm run lint; npm test -- --run tests/components/ComposerStop.test.tsx tests/hooks/useChatStreamStop.test.ts`
Expected: clean + green.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ChatPage.tsx
git commit -m "feat(chat): wire the Stop control through ChatPage (FR-15)"
```

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
