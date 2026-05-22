import { create } from "zustand";
import type { MemoryItem, MemoryStatus, MemoryScope } from "@/types/domain";
import { listMemories, patchMemory, deleteMemory, createMemory } from "@/lib/api";

/** Map a session id to a store-map key. A null session (empty chat with no
 *  backend session yet) is bucketed under 0 — real session ids are >= 1, so
 *  it can never collide with a real session's memory list. */
function key(sessionId: number | null): number {
  return sessionId ?? 0;
}

interface MemoriesState {
  memoriesBySession: Record<number, MemoryItem[]>;
  fetchMemories: (sessionId: number | null) => Promise<void>;
  addMemoryLocal: (
    sessionId: number | null,
    content: string,
    scope: MemoryScope,
  ) => Promise<void>;
  patchMemoryLocal: (
    sessionId: number | null,
    memoryId: number,
    patch: { content?: string; status?: MemoryStatus },
  ) => Promise<void>;
  deleteMemoryLocal: (
    sessionId: number | null,
    memoryId: number,
  ) => Promise<void>;
}

export const useMemoriesStore = create<MemoriesState>((set) => ({
  memoriesBySession: {},

  fetchMemories: async (sessionId) => {
    const items = await listMemories(sessionId);
    set((s) => ({
      memoriesBySession: { ...s.memoriesBySession, [key(sessionId)]: items },
    }));
  },

  addMemoryLocal: async (sessionId, content, scope) => {
    const created = await createMemory(content, scope, sessionId);
    const k = key(sessionId);
    set((s) => ({
      memoriesBySession: {
        ...s.memoriesBySession,
        [k]: [...(s.memoriesBySession[k] ?? []), created],
      },
    }));
  },

  patchMemoryLocal: async (sessionId, memoryId, patch) => {
    const updated = await patchMemory(memoryId, patch, sessionId);
    const k = key(sessionId);
    set((s) => ({
      memoriesBySession: {
        ...s.memoriesBySession,
        [k]: (s.memoriesBySession[k] ?? []).map((m) =>
          m.id === memoryId ? updated : m,
        ),
      },
    }));
  },

  deleteMemoryLocal: async (sessionId, memoryId) => {
    await deleteMemory(memoryId, sessionId);
    const k = key(sessionId);
    set((s) => ({
      memoriesBySession: {
        ...s.memoriesBySession,
        [k]: (s.memoriesBySession[k] ?? []).filter((m) => m.id !== memoryId),
      },
    }));
  },
}));
