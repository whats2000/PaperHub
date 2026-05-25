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
} from "@/types/domain";
import { createBackendSession } from "@/lib/api";

interface ChatState {
  sessions: ChatSession[];
  activeSessionId: number | null;
  _nextId: number;
  sidebarCollapsed: boolean;
  sidebarTab: "chats" | "references";
  composerDraft: string;
  referencesBySession: Record<number, ReferenceItem[]>;
  newSession: () => number;
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
  ensureBackendSession: (sessionId: number) => Promise<number>;
  // Cross-device sync (backend is source of truth)
  reconcileBackendSessions: (summaries: SessionSummary[]) => void;
  hydrateSessionMessages: (
    sessionId: number,
    messages: BackendMessage[],
  ) => void;
}

function deriveTitle(content: string): string {
  const trimmed = content.trim().replace(/\s+/g, " ");
  if (trimmed.length <= 40) return trimmed;
  const cut = trimmed.slice(0, 40);
  const lastSpace = cut.lastIndexOf(" ");
  return (lastSpace > 20 ? cut.slice(0, lastSpace) : cut) + "…";
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
      referencesBySession: {},

      newSession: () => {
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

      selectSession: (id) => set({ activeSessionId: id }),

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
          return {
            sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
              trace: [...(msg.trace ?? []), record],
            }),
          };
        }),

      setMessageTrace: (sessionId, run_id, trace) =>
        set((s) => ({
          sessions: patchMessageByRunId(s.sessions, sessionId, run_id, { trace }),
        })),

      finaliseMessage: (sessionId, run_id, content) =>
        set((s) => ({
          sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
            content,
            status: "ok",
          }),
        })),

      errorMessage: (sessionId, run_id, error) =>
        set((s) => ({
          sessions: patchMessageByRunId(s.sessions, sessionId, run_id, {
            status: "error",
            error,
          }),
        })),

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

      setSearchResults: (sessionId, runId, candidates) =>
        set((s) => ({
          sessions: patchMessageByRunId(s.sessions, sessionId, runId, {
            search_results: candidates,
          }),
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
              return summary ? { ...sess, title: summary.title } : sess;
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
              .map((m) => ({
                role: m.role as "user" | "assistant",
                content: m.content,
                run_id: m.run_id,
                status: "ok" as const,
                ...(m.routing_decision
                  ? { routing_decision: m.routing_decision }
                  : {}),
                ...(m.search_results
                  ? { search_results: m.search_results }
                  : {}),
                // Carry the replayed deck so the in-chat DeckChip survives a
                // refresh — the message record is the robust source of truth
                // (no race with a separate deck fetch).
                ...(m.deck ? { deck: m.deck } : {}),
              }));
            return { ...sess, messages: mapped };
          }),
        })),
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
