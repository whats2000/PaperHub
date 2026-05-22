import { create } from "zustand";
import type { MemoryItem, MemoryStatus } from "@/types/domain";
import { listMemories, patchMemory, deleteMemory } from "@/lib/api";

interface MemoriesState {
  memoriesBySession: Record<number, MemoryItem[]>;
  fetchMemories: (sessionId: number) => Promise<void>;
  patchMemoryLocal: (
    sessionId: number,
    memoryId: number,
    patch: { content?: string; status?: MemoryStatus },
  ) => Promise<void>;
  deleteMemoryLocal: (sessionId: number, memoryId: number) => Promise<void>;
}

export const useMemoriesStore = create<MemoriesState>((set) => ({
  memoriesBySession: {},

  fetchMemories: async (sessionId) => {
    const items = await listMemories(sessionId);
    set((s) => ({
      memoriesBySession: { ...s.memoriesBySession, [sessionId]: items },
    }));
  },

  patchMemoryLocal: async (sessionId, memoryId, patch) => {
    const updated = await patchMemory(memoryId, patch, sessionId);
    set((s) => ({
      memoriesBySession: {
        ...s.memoriesBySession,
        [sessionId]: (s.memoriesBySession[sessionId] ?? []).map((m) =>
          m.id === memoryId ? updated : m,
        ),
      },
    }));
  },

  deleteMemoryLocal: async (sessionId, memoryId) => {
    await deleteMemory(memoryId, sessionId);
    set((s) => ({
      memoriesBySession: {
        ...s.memoriesBySession,
        [sessionId]: (s.memoriesBySession[sessionId] ?? []).filter(
          (m) => m.id !== memoryId,
        ),
      },
    }));
  },
}));
