import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type {
  ChatMessage,
  ChatSession,
  RoutingDecision,
  ToolCallRecord,
} from "@/types/domain";

interface ChatState {
  sessions: ChatSession[];
  activeSessionId: number | null;
  _nextId: number;
  sidebarCollapsed: boolean;
  composerDraft: string;
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
  setComposerDraft: (text: string) => void;
  reset: () => void;
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
      composerDraft: "",

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

      setComposerDraft: (text) => set({ composerDraft: text }),

      reset: () =>
        set({ sessions: [], activeSessionId: null, _nextId: 1, composerDraft: "" }),
    }),
    {
      name: "paperhub-chat-v1",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        sessions: state.sessions,
        activeSessionId: state.activeSessionId,
        _nextId: state._nextId,
        sidebarCollapsed: state.sidebarCollapsed,
        composerDraft: state.composerDraft,
      }),
    },
  ),
);
