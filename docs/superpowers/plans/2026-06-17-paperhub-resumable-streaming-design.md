# Resumable Chat Streaming — Design (supersedes Part A of run-cancel)

> **Status:** DESIGN (not yet a task plan). Captures the architecture agreed with the
> user on 2026-06-17. The existing plan
> [`2026-06-16-paperhub-run-cancel-version-awareness.md`](2026-06-16-paperhub-run-cancel-version-awareness.md)
> **Part A is replaced** by this design; **Part B (version/changelog) is unaffected** and
> ships as written. A task-by-task plan will be derived from this doc after review.

## 1. Why this exists — the requirement that breaks the old design

The original Part A treated **Stop** as a purely client-side "retract the turn" action and
assumed a disconnect should end the turn. The user's actual requirement is the opposite:

1. **A disconnect is NOT a cancel.** Refresh, navigate away, close the tab, network drop,
   or viewing from another device must **not** stop generation. The run keeps producing on
   the backend.
2. **Reattach on return.** When the user comes back (reload, or a second device), they must
   be able to **rejoin the in-flight answer** and watch it finish.
3. **Cancellation is explicit-only.** The *only* thing that ends a run early is the user
   pressing the **Stop** button. Nothing else cancels.

### The crux (why this is a real redesign, not a tweak)

Today the agent/LLM work runs **inside the SSE generator** that the HTTP request drives
([`chat.py` `stream_events()`](../../../backend/src/paperhub/api/chat.py)). sse-starlette
cancels that generator when the client disconnects — so **a refresh currently kills the
run**. To satisfy requirement #1, the work must move **off the HTTP request** into a
backend-owned background task. Once the work is decoupled, requirements #2 and #3 follow
naturally (reattach = read the task's state; cancel = cancel the task).

There is no lighter shortcut: if generation must continue with **zero clients connected**,
it cannot live on a client connection.

## 2. Agreed decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D1 | **Decouple the run into a background `asyncio.Task`** registered in an in-process broker. | Only way a run survives a disconnect (D1 is the crux). Single-worker deployment makes an in-memory broker viable. |
| D2 | **Originating tab keeps its live SSE.** | Preserves smooth token-by-token UX for the common case; the SSE just *subscribes* to the background task instead of *being* it. |
| D3 | **Reattach (refresh / other device) is by POLLING** a run-state endpoint, not a second SSE. | User's call ("polling is great for reattach"). Simpler client lifecycle, robust across eviction/restart; near-live (progressive partial every ~1 s) is good enough for the rejoin case. |
| D4 | **Cancel ONLY via explicit Stop** → `POST /chat/cancel`. A bare disconnect never cancels. | Core requirement #3. |
| D5 | **On backend startup, mark any leftover `running` run as `interrupted`** (the in-memory broker did not survive the restart). | No ghost spinners; the returning client sees a clean interrupted turn it can retry. |
| D6 | **Single backend worker** (uvicorn `--reload` / no `--workers N`) is assumed and required. | The broker is process-local; cross-device cancel/poll only works if every request hits the same process. Already true in `start.ps1`. |
| D7 | **Reattach is high-fidelity** — the poll returns raw SSE **event deltas** (cursor-based) replayed through the same reducer as the live SSE, so trace + deck + citations + text all rebuild, not just the answer text. | User's call ("if possible reattach"). The handle already buffers every event, so deltas are free; one reducer serves both transports. |
| D8 | **`interrupted` is a distinct UI state** with a **Retry** affordance (not the plain error bubble). | User's call ("yep"). A restart-interrupted turn is recoverable; Retry re-sends the original user message. |

## 3. Architecture

```
                         ┌─────────────────────────── backend process ───────────────────────────┐
                         │                                                                         │
  POST /chat  ──────────▶│  create run row  ──▶  spawn BACKGROUND TASK  ──▶  RunBroker[run_id]      │
   (originating tab)     │        │                    (run_agent coro)        = RunHandle:         │
        ▲                │        │                         │  pushes events       • task           │
        │ SSE subscribe  │        └── return SSE that ◀──────┘  to handle          • event buffer    │
        │ (live tail)    │            subscribes to RunHandle (live tail)         • subscribers set  │
        │                │                                                        • snapshot state   │
        │                │                                                        • status / final   │
  (disconnect = just unsubscribe; task keeps running)                                                │
                         │                                                                         │
  GET /chat/runs/{id}/state  ◀── poll every ~1s ── returns snapshot (partial_text, status, steps)   │
   (reattach: refresh / other device)   │  falls back to DB if handle absent (done+evicted/restart) │
                         │                                                                         │
  POST /chat/cancel {run_id}  ─────────▶ cancel RunHandle.task + retract (delete msgs, status=cancelled)
                         └─────────────────────────────────────────────────────────────────────────┘
```

### 3.1 Backend components

**RunBroker** (`backend/src/paperhub/api/run_broker.py`, new) — process-local registry:

```python
RunBroker: dict[int, RunHandle]            # run_id -> handle; the single source of live truth
```

**RunHandle** — per-run live state:
- `task: asyncio.Task` — the background run coroutine (this is what `POST /chat/cancel` cancels).
- `events: list[dict]` — append-only buffer of every SSE event emitted so far (`session`,
  `routing_decision`, `tool_step`, `token`, `search_results`, `deck`, `final`/`error`). Lets a
  newly-subscribing SSE replay from the start.
- `subscribers: set[asyncio.Queue]` — live SSE listeners; each emitted event is appended to
  `events` **and** put on every subscriber queue.
- The **poll** endpoint reads directly from `events` (cursor-based deltas) — no separate
  snapshot needed; `events` IS the replayable state. (A `status` + `final_message_id` are kept
  for the terminal/DB-fallback path.)
- `status: 'running' | 'ok' | 'error' | 'cancelled' | 'interrupted'` and `done: asyncio.Event`.
- `evict_at: float | None` — set when terminal; GC after a short TTL (≈60 s) so the connected
  SSE and any in-flight poll observe the terminal state before the handle disappears.

**`run_agent(run_id, session_id, state, ...)`** — the background coroutine. It is the **current
body of `stream_events()`**, refactored so that everywhere it does
`yield {"event": ..., "data": ...}` today it instead calls `handle.emit(event)` (append to
buffer + fan out + update snapshot). It still:
- persists the user message (already done before work starts),
- runs the LangGraph subgraphs exactly as today,
- on success calls `_finalise(status="ok")` and emits `final`,
- on `Exception` calls `_finalise(status="error")` and emits `error`,
- on `CancelledError` (from Stop) lets it propagate — the cancel endpoint owns the DB cleanup,
- in `finally` resets the client-headers contextvar (as today) and marks the handle terminal.

**Endpoints** (`backend/src/paperhub/api/chat.py`):
- `POST /chat` — create run row, `asyncio.create_task(run_agent(...))`, register the handle,
  return `EventSourceResponse` of a thin subscriber generator
  (`replay handle.events, then drain a fresh subscriber queue until terminal`). **Client
  disconnect unsubscribes only.** The `run_id` is still emitted in the first `session` event.
- `GET /chat/runs/{run_id}/events?since={cursor}` — reattach poll, **high-fidelity** (D7). If
  the handle exists, return `{ status, events: handle.events[cursor:], next_cursor }` — the
  raw SSE event deltas (`session`/`routing_decision`/`tool_step`/`token`/`search_results`/
  `deck`/`final`/`error`). The client replays them through the **same reducer the live SSE
  uses**, so the Trace panel, Slides/deck, citations, and answer text all rebuild on reattach —
  not just the text. If the handle is absent (evicted after completion, or lost to a restart),
  **fall back to the DB**: return a terminal `status` and a synthetic single `final`/`error`
  event built from the persisted assistant message, so the client converges to the final state.
- `POST /chat/cancel {run_id}` — the **only** cancel path. `handle.task.cancel()` if present;
  then DB retract **guarded on `status='running'`** (delete the turn's messages + set
  `status='cancelled'`) so a Stop that races completion can't delete a finished answer. Works
  even with no handle (post-restart orphan): the guarded DB cleanup still clears it.

**Startup reconciliation** (`app.py` lifespan / `db` init): on boot,
`UPDATE runs SET status='interrupted', finished_at=now WHERE status='running'`, and ensure each
such run has a paired assistant row (insert a synthetic `interrupted` assistant message if it
has only a user row) so the **pair invariant** holds on first load after a restart.

### 3.2 Frontend behavior

**Originating tab (unchanged UX):** `useChatStream` opens the `POST /chat` SSE and renders
tokens live, exactly as today. Stop → `cancelRun(run_id)` + retract the pair + restore the text
to the composer (the explicit-Stop behavior from the old Part A is **kept**).

**Reattach (refresh / other device):** on session hydration, if a run is `running` (from
`GET /sessions/{id}/messages`, which now also returns each row's run `status`):
1. Render the pair as **user message + processing placeholder** (pair invariant; no bare user
   message).
2. Start a **poller**: `GET /chat/runs/{run_id}/events?since={cursor}` every ~1 s, replaying each
   returned event through the **same reducer the live SSE uses** (so trace/deck/citations/text
   all rebuild — high-fidelity, D7). Advance `cursor` by `next_cursor` each poll.
   - while `status==='running'`: keep replaying deltas — the answer + trace + deck visibly build.
   - on `status==='ok'`: replay the final `final` event, stop polling; optionally refetch
     `/sessions/{id}/messages` to settle the canonical row.
   - on `status` in `error|interrupted`: stop polling; show the error / **interrupted** bubble
     (interrupted is a distinct state with a Retry affordance — D8).
   - on `status==='cancelled'`: stop polling; remove the placeholder (the run was retracted on
     another tab).
3. The **Stop button still works** on a reattached turn — it resolves the `run_id` from the
   placeholder and calls `cancelRun(run_id)` (kills the same-process task + retracts).

**Distinguishing live-SSE from reattach-poll:** a same-tab live stream and a hydrated reattach
placeholder must not both poll. Introduce a distinct message status for the hydrated case
(e.g. `status: "processing"` for reattach vs `status: "streaming"` for the live local SSE) so
`useSessionsSync`'s existing "skip re-sync while streaming" guard and the new poller target the
right messages. (Exact status taxonomy is an open item — see §5.)

## 4. Edge cases & how they resolve

| Case | Behavior |
|------|----------|
| Refresh mid-run | SSE dies → task keeps running → reload hydrates `running` → poller rejoins → answer finishes on screen. |
| Second device mid-run | Same-process broker → poller on device B shows progressive partial → completes. |
| Explicit Stop (same tab) | `cancelRun` cancels the task NOW (owned task → reliable interrupt) + retract + restore text. |
| Stop races completion | DB retract guarded on `status='running'`; a just-`ok` run is untouched (no nuked answer). |
| Stop on a reattached turn | Poller's placeholder carries `run_id`; Stop calls `cancelRun(run_id)`; same-process task cancelled. |
| Backend restart mid-run | Broker lost; startup marks the run `interrupted` (+ paired assistant row) → returning client shows a clean interrupted turn (D5). |
| Run completes while user away | Task finishes, persists final, handle evicted after TTL → later poll falls back to DB → returning client sees the final answer. |
| Two tabs of the same user | Originating tab streams live; the other tab polls; both converge. |

## 5. Resolved items (decisions for the task plan)

1. **Status taxonomy — RESOLVED.** Backend `runs.status` ∈ `running | ok | error | cancelled |
   interrupted`. Frontend `ChatMessage.status` gains `"processing"` (reattach placeholder, drives
   the poller + Stop) and `"interrupted"` (distinct state with **Retry**, per D8). `"streaming"`
   stays the *live local SSE* marker so the two never both poll.
2. **Reattach fidelity — RESOLVED (D7).** Poll returns raw event **deltas** replayed through the
   live-SSE reducer → trace + deck + citations + text all rebuild. No separate snapshot shape.
3. **Poll cadence & stop conditions.** ~1 s while `running`; stop on terminal status, on session
   switch, and on tab hide (`visibilitychange`) to avoid background polling. (Interval tunable;
   1 s is the default.)
4. **Buffer/eviction memory bounds.** Per-run event buffer kept in full; handle evicted ≈60 s
   after terminal. Small for single-user self-host; revisit only if it ever bites.
5. **`run_agent` lifecycle ownership.** Background tasks `create_task`'d and held in a
   module-level live-task set (mirrors the MCP-daemon pattern) so they aren't GC'd mid-flight and
   are cancelled on app shutdown.
6. **Originating SSE replay.** The subscriber replays `handle.events` then tails — cheap
   insurance against the register→subscribe race; same code path the poll reattach uses.
7. **Tracing unaffected.** All `tool_calls` tracing stays inside `run_agent`, writing to the DB as
   today; the broker only mirrors the SSE event stream, not the trace.

## 6. Impact on the existing plan

- **Part A (A1–A6) is replaced** by this design. The pieces that survive: the Stop button
  (Composer) + the explicit-Stop retract/restore-to-composer behavior + the `cancelRun` client
  + `/chat/cancel` (now cancelling the *background task* and guarded-retracting). The new work:
  the run broker, the `run_agent` extraction, the `POST /chat` subscribe refactor, the
  `GET /chat/runs/{id}/state` poll endpoint, the startup reconciliation, and the frontend
  reattach poller.
- **Part B (B1–B10, version/changelog)** is **unchanged** and independent; it can proceed in
  parallel or first.
- The SRS FR-15 text needs updating from "client-side retract / disconnect ends the turn" to
  "runs are backend-owned and resumable; only explicit Stop cancels."

## 7. Verification posture (unchanged principle, new specifics)

pytest proves wiring; the **live `:8000` test is the real proof** and must cover the three
behaviors unit tests can't:
1. **Disconnect ≠ cancel:** start a `/chat`, drop the SSE mid-run, confirm via DB/trace that
   `tool_calls` keep accruing and `runs.status` stays `running` → then `ok` (the run finished
   with no client connected).
2. **Reattach:** after the drop, `GET /chat/runs/{id}/state` shows `partial_text` growing then
   a terminal `status` with the full answer.
3. **Explicit Stop stops the LLM:** `POST /chat/cancel` → `tool_calls` count frozen within
   ~1 s (owned-task cancel reliably interrupts), `runs.status='cancelled'`, messages deleted.
