import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  ChatMessage,
  ChatSession,
  RoutingDecision,
  ToolCallRecord,
  ReferenceItem,
  SearchResultCandidate,
  SessionSummary,
  BackendMessage,
  DeckEventData,
  AgentSummary,
  AgentUiEvent,
} from "@/types/domain";
import { createBackendSession, listSessionReferences } from "@/lib/api";
import { useMemoriesStore } from "@/store/memories";
import { useNavStore } from "@/store/nav";
import { useSlidesStore } from "@/store/slides";

interface ChatState {
  sessions: ChatSession[];
  activeSessionId: number | null;
  _nextId: number;
  sidebarCollapsed: boolean;
  sidebarTab: "chats" | "references";
  composerDraft: string;
  /** Bumped each time something asks to prefill the composer (e.g. a deck-chip
   *  button). The Composer watches it to focus + move the cursor to the end so
   *  the dropped-in prompt is obviously editable. Not persisted. */
  composerFocusSeq: number;
  referencesBySession: Record<number, ReferenceItem[]>;
  newSession: () => number;
  /** Add a session created by POST /sessions/{id}/fork: it already has a
   *  backend row (with copied history), so insert it with backend_session_id
   *  set + select it (useSessionsSync hydrates its messages on activation).
   *  Returns the local id. Idempotent on an already-present backend id. */
  addForkedSession: (
    backendId: number,
    title: string,
    forkedFromSessionId?: number | null,
    /** Set for a chat opened from an idea (Plan H3). */
    ideaId?: number | null,
  ) => number;
  selectSession: (id: number) => void;
  appendMessage: (sessionId: number, message: ChatMessage) => void;
  setRouting: (
    sessionId: number,
    run_id: number,
    decision: RoutingDecision,
  ) => void;
  appendToken: (sessionId: number, run_id: number, text: string) => void;
  appendTrace: (
    sessionId: number,
    run_id: number,
    record: ToolCallRecord,
  ) => void;
  /** Replace a message's trace wholesale — used to cache a lazily-fetched
   *  trace onto a replayed assistant message (see GET …/runs/{id}/trace). */
  setMessageTrace: (
    sessionId: number,
    run_id: number,
    trace: ToolCallRecord[],
  ) => void;
  finaliseMessage: (
    sessionId: number,
    run_id: number,
    content: string,
  ) => void;
  errorMessage: (sessionId: number, run_id: number, error: string) => void;
  failPendingAssistant: (sessionId: number, error: string) => void;
  patchAssistantRunId: (sessionId: number, runId: number) => void;
  patchSessionBackendId: (sessionId: number, backendId: number) => void;
  deleteSession: (sessionId: number) => ChatSession | null;
  restoreSession: (session: ChatSession, atIndex: number) => void;
  removeMessage: (sessionId: number, messageIndex: number) => void;
  toggleSidebar: () => void;
  setSidebarTab: (tab: "chats" | "references") => void;
  setComposerDraft: (text: string) => void;
  /** Prefill the composer with an editable prompt + request focus (does NOT
   *  send). Used by deck-chip Generate/Edit affordances. */
  requestComposerText: (text: string) => void;
  reset: () => void;
  // References
  setReferences: (backendSessionId: number, refs: ReferenceItem[]) => void;
  patchReferenceEnabled: (
    backendSessionId: number,
    papersId: number,
    enabled: boolean,
  ) => void;
  removeReferenceLocal: (backendSessionId: number, papersId: number) => void;
  appendReferenceLocal: (
    backendSessionId: number,
    ref: ReferenceItem,
  ) => void;
  setSearchResults: (
    sessionId: number,
    runId: number,
    candidates: SearchResultCandidate[],
  ) => void;
  setDeckOnMessage: (sessionId: number, deck: DeckEventData | null) => void;
  /** Agent turns (H4): apply one agent SSE event — `agent_summary`,
   *  `answer_reset`, `paper_added`, `memory_saved`, `confirm_request` —
   *  to the turn's assistant message (by run id, else the last assistant
   *  message: the UI events carry no run id). Shared by the live stream and
   *  the reattach poller. Unknown names are ignored. */
  applyAgentEvent: (
    sessionId: number,
    runId: number | null,
    name: string,
    data: unknown,
  ) => void;
  /** Merge `patch` into one agent UI event's data (a confirm card's
   *  status/result, a chip's `undone`). */
  patchAgentEvent: (
    sessionId: number,
    runId: number,
    index: number,
    patch: Record<string, unknown>,
  ) => void;
  /** Per-chat auto-approve switch (mirrors `chat_sessions.auto_approve`). */
  setAutoApprove: (sessionId: number, value: boolean) => void;
  /** Remove the trailing streaming assistant placeholder AND its paired user
   *  message (never leave a user message without a reply). Returns the user
   *  message text so the caller can restore it to the composer. */
  retractTurn: (sessionId: number) => string;
  /** Stop pressed (H4 review #5): a turn whose agent already acted (paper
   *  added, memory saved) is kept as a `stopped` stub with those chips (its
   *  pending confirmations become dismissed, as the backend does). Any other
   *  turn stays in place as `stopping` until the backend answers
   *  (`retractStopping` / `keepStopping`). */
  stopTurn: (sessionId: number) => { kept: boolean };
  /** The backend retracted the stopping turn: mark it exiting (it animates
   *  out, then `removeExiting` drops it). Returns its user text for the
   *  composer. */
  retractStopping: (sessionId: number) => string;
  /** The backend kept the stopping turn (it had acted): make it the stopped
   *  stub with the run's chips (from the backend's replay of that run). */
  keepStopping: (sessionId: number, events: AgentUiEvent[] | null) => void;
  /** Drop the messages `stopTurn` marked as exiting (only those: a turn sent
   *  meanwhile stays). */
  removeExiting: (sessionId: number) => void;
  ensureBackendSession: (sessionId: number) => Promise<number>;
  // Cross-device sync (backend is source of truth)
  reconcileBackendSessions: (summaries: SessionSummary[]) => void;
  hydrateSessionMessages: (
    sessionId: number,
    messages: BackendMessage[],
  ) => void;
  /** A10 D7: Shared event dispatcher used by both the live SSE path and the
   *  reattach poller — single source of truth for event→store mutations. */
  applyRunEvent: (
    sessionId: number,
    event: { event: string; data: string },
  ) => void;
}

function deriveTitle(content: string): string {
  const trimmed = content.trim().replace(/\s+/g, " ");
  if (trimmed.length <= 40) return trimmed;
  const cut = trimmed.slice(0, 40);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > 20 ? cut.slice(0, lastSpace) : cut) + "…";
}

/** Patch the assistant message of `runId`, or — when it is null or not
 *  found — the session's last assistant message. */
function patchTurnMessage(
  sessions: ChatSession[],
  sessionId: number,
  runId: number | null,
  fn: (m: ChatMessage) => ChatMessage,
): ChatSession[] {
  return sessions.map((sess) => {
    if (sess.id !== sessionId) return sess;
    let idx =
      runId === null
        ? -1
        : sess.messages.findIndex(
            (m) => m.role === "assistant" && m.run_id === runId,
          );
    if (idx < 0) idx = sess.messages.map((m) => m.role).lastIndexOf("assistant");
    if (idx < 0) return sess;
    const messages = [...sess.messages];
    messages[idx] = fn(messages[idx]!);
    return { ...sess, messages };
  });
}

const AGENT_UI_EVENTS = new Set(["paper_added", "memory_saved", "confirm_request"]);

/** The identity of an agent UI event: replays (the reattach poller restarts
 *  at cursor 0) must not add the same card twice. */
function agentEventKey(ev: AgentUiEvent): string {
  switch (ev.event) {
    case "confirm_request":
      return `confirm:${ev.data.action_id}`;
    case "paper_added":
      return `paper:${ev.data.papers_id}`;
    case "memory_saved":
      return `memory:${ev.data.memory_id}`;
  }
}

/** A search batch's identity: its candidates' keys, in order. */
function batchKey(batch: SearchResultCandidate[]): string {
  return JSON.stringify(batch.map((c) => c.paper_id));
}

/**
 * Mutates a single assistant message matched by run_id inside the given
 * session's messages array.  No-ops silently if the session or message is
 * not found — callers don't need to guard.
 */
function patchMessageByRunId(
  sessions: ChatSession[],
  sessionId: number,
  run_id: number,
  patch: Partial<ChatMessage>,
): ChatSession[] {
  return sessions.map((sess) => {
    if (sess.id !== sessionId) return sess;
    return {
      ...sess,
      messages: sess.messages.map((m) =>
        m.run_id === run_id && m.role === "assistant" ? { ...m, ...patch } : m,
      ),
    };
  });
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      sessions: [],
      activeSessionId: null,
      _nextId: 1,
      sidebarCollapsed: false,
      sidebarTab: "chats",
      composerDraft: "",
      composerFocusSeq: 0,
      referencesBySession: {},

      newSession: () => {
        useNavStore.getState().setPage("chat");
        const id = get()._nextId;
        set((s) => ({
          sessions: [
            ...s.sessions,
            { id, title: "New chat", messages: [], backend_session_id: null },
          ],
          activeSessionId: id,
          _nextId: s._nextId + 1,
        }));
        return id;
      },

      // Deliberately does not reset the page: forking only starts from the chat page.
      addForkedSession: (backendId, title, forkedFromSessionId = null, ideaId = null) => {
        const existing = get().sessions.find(
          (s) => s.backend_session_id === backendId,
        );
        if (existing) {
          set({ activeSessionId: existing.id });
          return existing.id;
        }
        const id = get()._nextId;
        set((s) => ({
          sessions: [
            {
              id,
              title,
              messages: [],
              backend_session_id: backendId,
              forked_from_session_id: forkedFromSessionId,
              idea_id: ideaId,
            },
            ...s.sessions,
          ],
          activeSessionId: id,
          _nextId: s._nextId + 1,
        }));
        return id;
      },

      selectSession: (id) => {
        useNavStore.getState().setPage("chat");
        set({ activeSessionId: id });
      },

      appendMessage: (sessionId, message) =>
        set((s) => ({
          sessions: s.sessions.map((sess) => {
            if (sess.id !== sessionId) return sess;
            const isFirstUser =
              message.role === "user" &&
              sess.title === "New chat" &&
              !sess.messages.some((m) => m.role === "user");
            return {
              ...sess,
              title: isFirstUser
                ? deriveTitle(message.content)
                : sess.title,
              messages: [...sess.messages, message],
            };
          }),
        })),

      setRouting: (sessionId, run_id, decision) =>
        set((s) => ({
          sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
            routing_decision: decision,
          }),
        })),

      appendToken: (sessionId, run_id, text) =>
        set((s) => {
          const sess = s.sessions.find((x) => x.id === sessionId);
          const msg = sess?.messages.find(
            (m) => m.run_id === run_id && m.role === "assistant",
          );
          if (!msg) return s;
          return {
            sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
              content: msg.content + text,
            }),
          };
        }),

      appendTrace: (sessionId, run_id, record) =>
        set((s) => {
          const sess = s.sessions.find((x) => x.id === sessionId);
          const msg = sess?.messages.find(
            (m) => m.run_id === run_id && m.role === "assistant",
          );
          if (!msg) return s;
          const prev = msg.trace ?? [];
          // Two kinds of tool_step arrive on the wire:
          //  • REAL steps (step_index >= 0) — persisted tool_calls rows. Each
          //    graph node's flush re-drains from -1 with its own dedup set, so
          //    an earlier node's steps (e.g. router#0) get re-emitted by a later
          //    node. Dedup by step_index so a re-emit replaces, never stacks.
          //  • HEARTBEATS (step_index < 0) — synthetic, live-only stage beats
          //    emitted every ~15s during a silent phase. Collapse to a SINGLE
          //    live marker (the latest) instead of one row per beat.
          const realSteps = prev.filter((r) => r.step_index >= 0);
          const liveBeat = prev.filter((r) => r.step_index < 0);
          let trace: ToolCallRecord[];
          if (record.step_index < 0) {
            // Newest beat supersedes any prior beat; keep it after the steps.
            trace = [...realSteps, record];
          } else {
            const deduped = realSteps.filter(
              (r) => r.step_index !== record.step_index,
            );
            deduped.push(record);
            deduped.sort((a, b) => a.step_index - b.step_index);
            // Preserve the current live beat at the tail (it shows the phase in
            // progress); a newer beat will replace it.
            trace = [...deduped, ...liveBeat];
          }
          return {
            sessions: patchMessageByRunId(s.sessions, sessionId, run_id, { trace }),
          };
        }),

      setMessageTrace: (sessionId, run_id, trace) =>
        set((s) => ({
          sessions: patchMessageByRunId(s.sessions, sessionId, run_id, { trace }),
        })),

      finaliseMessage: (sessionId, run_id, content) =>
        set((s) => {
          // Drop the live heartbeat beat — the turn is done, nothing is "in
          // progress" anymore (the real steps remain).
          const msg = s.sessions
            .find((x) => x.id === sessionId)
            ?.messages.find((m) => m.run_id === run_id && m.role === "assistant");
          const trace = (msg?.trace ?? []).filter((r) => r.step_index >= 0);
          return {
            sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
              content,
              status: "ok",
              trace,
            }),
          };
        }),

      errorMessage: (sessionId, run_id, error) =>
        set((s) => {
          const msg = s.sessions
            .find((x) => x.id === sessionId)
            ?.messages.find((m) => m.run_id === run_id && m.role === "assistant");
          const trace = (msg?.trace ?? []).filter((r) => r.step_index >= 0);
          return {
            sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
              status: "error",
              error,
              trace,
            }),
          };
        }),

      failPendingAssistant: (sessionId, error) =>
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === sessionId
              ? {
                  ...sess,
                  messages: sess.messages.map((m, i, arr) =>
                    i === arr.length - 1 &&
                    m.role === "assistant" &&
                    (m.status === "streaming" || m.status === undefined)
                      ? { ...m, status: "error", error }
                      : m,
                  ),
                }
              : sess,
          ),
        })),

      patchAssistantRunId: (sessionId, runId) =>
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === sessionId
              ? {
                  ...sess,
                  messages: sess.messages.map((m, i, arr) =>
                    i === arr.length - 1 &&
                    m.role === "assistant" &&
                    m.run_id === null
                      ? { ...m, run_id: runId }
                      : m,
                  ),
                }
              : sess,
          ),
        })),

      patchSessionBackendId: (sessionId, backendId) =>
        set((state) => {
          const session = state.sessions.find((s) => s.id === sessionId);
          if (!session || session.backend_session_id !== null) return state;
          return {
            sessions: state.sessions.map((s) =>
              s.id === sessionId
                ? { ...s, backend_session_id: backendId }
                : s,
            ),
          };
        }),

      deleteSession: (sessionId) => {
        const state = get();
        const idx = state.sessions.findIndex((s) => s.id === sessionId);
        if (idx < 0) return null;
        const removed = state.sessions[idx]!;
        set({
          sessions: state.sessions.filter((s) => s.id !== sessionId),
          activeSessionId:
            state.activeSessionId === sessionId
              ? null
              : state.activeSessionId,
        });
        return removed;
      },

      restoreSession: (session, atIndex) =>
        set((s) => {
          const next = [...s.sessions];
          next.splice(Math.min(atIndex, next.length), 0, session);
          return { sessions: next };
        }),

      removeMessage: (sessionId, messageIndex) =>
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === sessionId
              ? {
                  ...sess,
                  messages: sess.messages.filter((_, i) => i !== messageIndex),
                }
              : sess,
          ),
        })),

      toggleSidebar: () =>
        set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),

      setSidebarTab: (tab) => set({ sidebarTab: tab }),

      setComposerDraft: (text) => set({ composerDraft: text }),

      requestComposerText: (text) =>
        set((s) => ({
          composerDraft: text,
          composerFocusSeq: s.composerFocusSeq + 1,
        })),

      retractTurn: (sessionId) => {
        let restored = "";
        set((s) => ({
          sessions: s.sessions.map((sess) => {
            if (sess.id !== sessionId) return sess;
            const msgs = [...sess.messages];
            // drop the trailing streaming assistant placeholder...
            if (msgs.length > 0 && msgs[msgs.length - 1]!.role === "assistant") msgs.pop();
            // ...and its paired user message (never leave a user msg without a reply),
            // returning its text so the caller restores it to the composer.
            if (msgs.length > 0 && msgs[msgs.length - 1]!.role === "user") {
              restored = msgs[msgs.length - 1]!.content;
              msgs.pop();
            }
            return { ...sess, messages: msgs };
          }),
        }));
        return restored;
      },

      stopTurn: (sessionId) => {
        let result = { kept: false };
        set((s) => ({
          sessions: s.sessions.map((sess) => {
            if (sess.id !== sessionId) return sess;
            const msgs = [...sess.messages];
            const lastIdx = msgs.length - 1;
            const last = msgs[lastIdx];
            if (last === undefined || last.role !== "assistant") return sess;
            const acted = (last.agent_events ?? []).some(
              (e) => e.event === "paper_added" || e.event === "memory_saved",
            );
            if (acted) {
              msgs[lastIdx] = {
                ...last,
                status: "stopped",
                content: "",
                agent_events: (last.agent_events ?? []).map((e) =>
                  e.event === "confirm_request" &&
                  (e.data.status ?? "pending") === "pending"
                    ? { ...e, data: { ...e.data, status: "dismissed" } }
                    : e,
                ),
              };
              result = { kept: true };
              return { ...sess, messages: msgs };
            }
            msgs[lastIdx] = { ...last, stopping: true };
            return { ...sess, messages: msgs };
          }),
        }));
        return result;
      },

      retractStopping: (sessionId) => {
        let restored = "";
        set((s) => ({
          sessions: s.sessions.map((sess) => {
            if (sess.id !== sessionId) return sess;
            const idx = sess.messages.findIndex((m) => m.stopping);
            if (idx < 0) return sess;
            const msgs = [...sess.messages];
            msgs[idx] = { ...msgs[idx]!, stopping: false, exiting: true };
            const prev = msgs[idx - 1];
            if (prev?.role === "user") {
              msgs[idx - 1] = { ...prev, exiting: true };
              restored = prev.content;
            }
            return { ...sess, messages: msgs };
          }),
        }));
        return restored;
      },

      keepStopping: (sessionId, events) =>
        set((s) => ({
          sessions: s.sessions.map((sess) => {
            if (sess.id !== sessionId) return sess;
            const idx = sess.messages.findIndex((m) => m.stopping);
            if (idx < 0) return sess;
            const msgs = [...sess.messages];
            const m = msgs[idx]!;
            msgs[idx] = {
              ...m,
              stopping: false,
              status: "stopped",
              content: "",
              agent_events: events && events.length > 0 ? events : m.agent_events,
            };
            return { ...sess, messages: msgs };
          }),
        })),

      removeExiting: (sessionId) =>
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === sessionId
              ? { ...sess, messages: sess.messages.filter((m) => !m.exiting) }
              : sess,
          ),
        })),

      reset: () =>
        set({
          sessions: [],
          activeSessionId: null,
          _nextId: 1,
          composerDraft: "",
          referencesBySession: {},
        }),

      setReferences: (backendSessionId, refs) =>
        set((s) => ({
          referencesBySession: {
            ...s.referencesBySession,
            [backendSessionId]: refs,
          },
        })),

      patchReferenceEnabled: (backendSessionId, papersId, enabled) =>
        set((s) => {
          const existing = s.referencesBySession[backendSessionId] ?? [];
          return {
            referencesBySession: {
              ...s.referencesBySession,
              [backendSessionId]: existing.map((r) =>
                r.papers_id === papersId ? { ...r, enabled } : r,
              ),
            },
          };
        }),

      removeReferenceLocal: (backendSessionId, papersId) =>
        set((s) => {
          const existing = s.referencesBySession[backendSessionId] ?? [];
          return {
            referencesBySession: {
              ...s.referencesBySession,
              [backendSessionId]: existing.filter(
                (r) => r.papers_id !== papersId,
              ),
            },
          };
        }),

      appendReferenceLocal: (backendSessionId, ref) =>
        set((s) => {
          const existing = s.referencesBySession[backendSessionId] ?? [];
          if (existing.some((r) => r.papers_id === ref.papers_id)) {
            return s;
          }
          return {
            referencesBySession: {
              ...s.referencesBySession,
              [backendSessionId]: [...existing, ref],
            },
          };
        }),

      // An agent turn may search several times: each event is its own batch
      // (one card list per search); `search_results` stays the flat union.
      setSearchResults: (sessionId, runId, candidates) =>
        set((s) => ({
          sessions: patchTurnMessage(s.sessions, sessionId, runId, (m) => {
            const prior = m.search_result_batches ?? [];
            // Idempotent: a replayed batch (same candidates) is not re-added.
            const key = batchKey(candidates);
            if (prior.some((b) => batchKey(b) === key)) return m;
            const batches = [...prior, candidates];
            return {
              ...m,
              search_result_batches: batches,
              search_results: batches.flat(),
            };
          }),
        })),

      applyAgentEvent: (sessionId, runId, name, data) => {
        set((s) => {
          let fn: ((m: ChatMessage) => ChatMessage) | null = null;
          if (name === "agent_summary") {
            const d = data as AgentSummary;
            const summary: AgentSummary = {
              papers_read: d.papers_read,
              steps: d.steps,
              tools: d.tools,
            };
            fn = (m) => ({ ...m, agent_summary: summary });
          } else if (name === "answer_reset") {
            // The streamed text was a tool-calling preamble: drop it.
            fn = (m) => ({ ...m, content: "" });
          } else if (AGENT_UI_EVENTS.has(name)) {
            const ev = { event: name, data } as AgentUiEvent;
            if (ev.event === "confirm_request" && !ev.data.status) {
              ev.data = { ...ev.data, status: "pending" };
            } else if (ev.event !== "confirm_request" && ev.data.undoable === undefined) {
              ev.data = { ...ev.data, undoable: true };
            }
            const key = agentEventKey(ev);
            // Idempotent: a replayed event keeps the card's local state.
            fn = (m) =>
              (m.agent_events ?? []).some((e) => agentEventKey(e) === key)
                ? m
                : { ...m, agent_events: [...(m.agent_events ?? []), ev] };
          }
          if (fn === null) return s;
          return { sessions: patchTurnMessage(s.sessions, sessionId, runId, fn) };
        });
        // The agent changed this chat's papers / memories server-side: refresh
        // the panels that only pull on session changes (live and reattach).
        const backendId = get().sessions.find((x) => x.id === sessionId)
          ?.backend_session_id;
        if (backendId == null) return;
        if (name === "paper_added") {
          void listSessionReferences(backendId)
            .then((refs) => get().setReferences(backendId, refs))
            .catch(() => undefined);
        } else if (name === "memory_saved") {
          void useMemoriesStore
            .getState()
            .fetchMemories(backendId)
            .catch(() => undefined);
        }
      },

      patchAgentEvent: (sessionId, runId, index, patch) =>
        set((s) => ({
          sessions: patchTurnMessage(s.sessions, sessionId, runId, (m) => {
            const events = [...(m.agent_events ?? [])];
            const ev = events[index];
            if (!ev) return m;
            events[index] = { ...ev, data: { ...ev.data, ...patch } } as AgentUiEvent;
            return { ...m, agent_events: events };
          }),
        })),

      setAutoApprove: (sessionId, value) =>
        set((s) => ({
          sessions: s.sessions.map((sess) =>
            sess.id === sessionId ? { ...sess, auto_approve: value } : sess,
          ),
        })),

      setDeckOnMessage: (sessionId, deck) =>
        set((s) => {
          // Find the streaming/last assistant message for this session and
          // attach the deck. We use run_id if available (same pattern as
          // setSearchResults but the run_id comes from the deck's session_id,
          // not a run_id field). Fall back to patching the last assistant msg.
          // A null `deck` CLEARS the chip (deck deleted upstream / 404 on
          // re-hydration) — we patch `deck: undefined` so MessageBubble's
          // `message.deck !== undefined` guard stops rendering it.
          const patchValue = deck ?? undefined;
          const sess = s.sessions.find((x) => x.id === sessionId);
          if (!sess) return s;
          // Find the last assistant message (streaming or most-recent ok)
          const lastAssistantMsg = [...sess.messages]
            .reverse()
            .find((m) => m.role === "assistant");
          if (!lastAssistantMsg || lastAssistantMsg.run_id === null) {
            // No run_id yet — fall back to patching by position (last assistant msg)
            return {
              sessions: s.sessions.map((se) => {
                if (se.id !== sessionId) return se;
                const msgs = [...se.messages];
                const idx = msgs.map((m) => m.role).lastIndexOf("assistant");
                if (idx < 0) return se;
                msgs[idx] = { ...msgs[idx]!, deck: patchValue };
                return { ...se, messages: msgs };
              }),
            };
          }
          return {
            sessions: patchMessageByRunId(
              s.sessions,
              sessionId,
              lastAssistantMsg.run_id,
              { deck: patchValue },
            ),
          };
        }),

      ensureBackendSession: async (sessionId) => {
        const session = get().sessions.find((s) => s.id === sessionId);
        if (!session) throw new Error(`session ${sessionId} not found`);
        if (session.backend_session_id !== null) return session.backend_session_id;
        const backendId = await createBackendSession();
        get().patchSessionBackendId(sessionId, backendId);
        return backendId;
      },

      reconcileBackendSessions: (summaries) =>
        set((s) => {
          // STRICT MIRROR: the backend DB is the single source of truth for
          // which chats exist. The frontend list must match it exactly so a
          // chat deleted on one device disappears on every other device.
          //
          //   Keep a local session ONLY if:
          //     - it has NO backend row yet (an unsent draft — never a
          //       cross-device entity), OR
          //     - the DB still lists it (matched by backend_session_id).
          //
          //   Everything else is pruned — including a session that still has
          //   messages cached locally but whose backend row is gone (deleted
          //   elsewhere). That cached copy is exactly the "deleted in A but
          //   still in B" ghost; strict mirror removes it.
          const byBackendId = new Map(summaries.map((x) => [x.id, x]));

          const kept = s.sessions
            .filter(
              (sess) =>
                sess.backend_session_id === null ||
                byBackendId.has(sess.backend_session_id),
            )
            .map((sess) => {
              // Backend owns the title (derived from the first message).
              const summary =
                sess.backend_session_id !== null
                  ? byBackendId.get(sess.backend_session_id)
                  : undefined;
              // Backend owns the title AND the fork lineage (the parent may be
              // purged, flipping forked_from to null — keep the mirror exact).
              return summary
                ? {
                    ...sess,
                    title: summary.title,
                    forked_from_session_id:
                      summary.forked_from_session_id ?? null,
                    idea_id: summary.idea_id ?? null,
                    auto_approve: summary.auto_approve ?? false,
                  }
                : sess;
            });

          const localBackendIds = new Set(
            kept
              .map((sess) => sess.backend_session_id)
              .filter((id): id is number => id !== null),
          );

          let nextId = s._nextId;
          const additions: ChatSession[] = [];
          for (const summary of summaries) {
            if (localBackendIds.has(summary.id)) continue;
            additions.push({
              id: nextId,
              title: summary.title,
              messages: [],
              backend_session_id: summary.id,
              forked_from_session_id: summary.forked_from_session_id ?? null,
              idea_id: summary.idea_id ?? null,
              auto_approve: summary.auto_approve ?? false,
            });
            nextId += 1;
          }

          // Backend list is newest-first; show backend sessions ahead of any
          // local-only draft, preserving backend order.
          const sessions = [...additions, ...kept];
          // If the active session was pruned (deleted elsewhere), clear it so
          // the UI doesn't point at a chat that no longer exists.
          const activeStillExists = sessions.some(
            (sess) => sess.id === s.activeSessionId,
          );

          // No-op guard: nothing added, nothing pruned/retitled, active intact.
          if (
            additions.length === 0 &&
            kept.length === s.sessions.length &&
            kept.every((sess, i) => sess === s.sessions[i]) &&
            activeStillExists
          ) {
            return s;
          }
          return {
            sessions,
            _nextId: nextId,
            activeSessionId: activeStillExists ? s.activeSessionId : null,
          };
        }),

      hydrateSessionMessages: (sessionId, messages) =>
        set((s) => ({
          sessions: s.sessions.map((sess) => {
            if (sess.id !== sessionId) return sess;
            // Replace the local copy with the DB's (the backend is the source
            // of truth for the chat record), EXCEPT while a turn is streaming
            // — the DB doesn't hold the in-flight message yet, so replacing
            // would clobber live state. This guard also covers a fetch that
            // resolves mid-turn.
            if (sess.messages.some((m) => m.status === "streaming")) return sess;
            const mapped: ChatMessage[] = messages
              .filter((m) => m.role === "user" || m.role === "assistant")
              .map((m) => {
                // A10: derive status from run_status for assistant rows.
                // interrupted → "interrupted"; error → "error"; else "ok".
                let status: ChatMessage["status"] = "ok";
                if (m.role === "assistant") {
                  if (m.run_status === "interrupted") status = "interrupted";
                  else if (m.run_status === "error") status = "error";
                  // A stopped turn that had already acted keeps a stub.
                  else if (m.run_status === "cancelled") status = "stopped";
                }
                return {
                  role: m.role as "user" | "assistant",
                  content: m.content,
                  run_id: m.run_id,
                  status,
                  ...(m.routing_decision
                    ? { routing_decision: m.routing_decision }
                    : {}),
                  ...(m.search_results
                    ? { search_results: m.search_results }
                    : {}),
                  ...(m.search_result_batches && m.search_result_batches.length > 0
                    ? { search_result_batches: m.search_result_batches }
                    : {}),
                  ...(m.agent_summary ? { agent_summary: m.agent_summary } : {}),
                  ...(m.agent_events && m.agent_events.length > 0
                    ? { agent_events: m.agent_events }
                    : {}),
                  // Carry the replayed deck so the in-chat DeckChip survives a
                  // refresh — the message record is the robust source of truth
                  // (no race with a separate deck fetch).
                  ...(m.deck ? { deck: m.deck } : {}),
                };
              });
            // A10: Pair-invariant placeholder — if the last message is a user
            // whose run_status === "running" with no following assistant row,
            // append a synthetic processing placeholder so Stop shows and the
            // reattach poller has something to drive.
            const last = mapped[mapped.length - 1];
            const lastBackend = messages.filter(
              (m) => m.role === "user" || m.role === "assistant",
            )[mapped.length - 1];
            if (
              last !== undefined &&
              last.role === "user" &&
              lastBackend?.run_status === "running"
            ) {
              mapped.push({
                role: "assistant",
                content: "",
                run_id: last.run_id,
                status: "processing",
              });
            }
            return { ...sess, messages: mapped };
          }),
        })),

      applyRunEvent: (sessionId, event) => {
        // A10 D7: thin dispatcher — parse the SSE data and route to the
        // EXISTING store mutations so the poller has identical fidelity to
        // the live SSE path. Wrap parse in try/catch to swallow malformed events.
        try {
          const data = JSON.parse(event.data) as Record<string, unknown>;
          const store = get();
          const run_id =
            typeof data.run_id === "number" ? data.run_id : null;
          switch (event.event) {
            case "token":
              if (run_id !== null && typeof data.text === "string") {
                store.appendToken(sessionId, run_id, data.text);
              }
              break;
            case "final":
              if (run_id !== null && typeof data.content === "string") {
                store.finaliseMessage(sessionId, run_id, data.content);
              }
              break;
            case "error":
              if (run_id !== null && typeof data.message === "string") {
                store.errorMessage(sessionId, run_id, data.message);
              }
              break;
            case "routing_decision":
              if (run_id !== null && data.decision !== undefined) {
                store.setRouting(
                  sessionId,
                  run_id,
                  data.decision as import("@/types/domain").RoutingDecision,
                );
              }
              break;
            case "tool_step": {
              const rec = (data as { record?: ToolCallRecord }).record;
              if (rec && typeof rec.run_id === "number") {
                store.appendTrace(sessionId, rec.run_id, rec);
              }
              break;
            }
            case "search_results":
              if (run_id !== null && Array.isArray(data.candidates)) {
                store.setSearchResults(
                  sessionId,
                  run_id,
                  data.candidates as import("@/types/domain").SearchResultCandidate[],
                );
              }
              break;
            case "deck": {
              const d = data as unknown as import("@/types/domain").DeckEventData;
              store.setDeckOnMessage(sessionId, d);
              // Mirror the live SSE path: also push the deck into the slides
              // store so the Slides panel receives it on reattach.
              const slidesStore = useSlidesStore.getState();
              slidesStore.setDeck(d.session_id, d);
              if (slidesStore.currentPageBySession[d.session_id] === undefined) {
                slidesStore.setCurrentPage(d.session_id, 1);
              }
              break;
            }
            case "agent_summary":
            case "answer_reset":
            case "paper_added":
            case "memory_saved":
            case "confirm_request":
              store.applyAgentEvent(sessionId, run_id, event.event, data);
              break;
            // "session" + unknown events: no-op (placeholder carries run_id)
            default:
              break;
          }
        } catch {
          // Ignore malformed events — don't let a single bad payload kill reattach.
        }
      },
    }),
    {
      name: "paperhub-chat-v1",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
        _nextId: state._nextId,
        sidebarCollapsed: state.sidebarCollapsed,
        sidebarTab: state.sidebarTab,
        composerDraft: state.composerDraft,
      }),
    },
  ),
);
