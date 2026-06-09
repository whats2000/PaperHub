// frontend/src/store/settings.ts
import { create } from "zustand";

import { getSettings, patchSettings, type SettingsConfig } from "../lib/api";

interface SettingsState {
  isOpen: boolean;
  config: SettingsConfig | null;
  loading: boolean;
  error: boolean;
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
  error: false,
  restartPending: [],
  open: () => {
    set({ isOpen: true });
    if (!get().config) void get().fetchConfig();
  },
  close: () => set({ isOpen: false }),
  fetchConfig: async () => {
    set({ loading: true, error: false });
    try {
      const config = await getSettings();
      set({ config });
    } catch {
      // Surfaced as a retry state in the modal; never an unhandled rejection.
      set({ error: true });
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
