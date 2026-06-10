// frontend/src/store/settings.ts
import { create } from "zustand";

import {
  getModelOptions,
  getReadiness,
  getSettings,
  patchSettings,
  type SettingsConfig,
  type SettingsModelOptions,
  type SettingsReadiness,
} from "../lib/api";

interface SettingsState {
  isOpen: boolean;
  config: SettingsConfig | null;
  loading: boolean;
  error: boolean;
  restartPending: string[];
  /** First-run gate state; null until first fetch. */
  readiness: SettingsReadiness | null;
  /** Per-provider model autocomplete options; null until fetched. */
  modelOptions: SettingsModelOptions | null;
  /** The first-run tour auto-shows while not ready unless the user dismisses it
   *  (per browser session). Reopenable from the account menu. */
  welcomeDismissed: boolean;
  open: () => void;
  close: () => void;
  dismissWelcome: () => void;
  reopenWelcome: () => void;
  fetchConfig: () => Promise<void>;
  fetchReadiness: () => Promise<void>;
  fetchModelOptions: () => Promise<void>;
  save: (patch: Record<string, string | null>) => Promise<void>;
}

export const useSettingsStore = create<SettingsState>()((set, get) => ({
  isOpen: false,
  config: null,
  loading: false,
  error: false,
  restartPending: [],
  readiness: null,
  modelOptions: null,
  welcomeDismissed: false,
  open: () => {
    set({ isOpen: true });
    if (!get().config) void get().fetchConfig();
    void get().fetchModelOptions();
  },
  close: () => set({ isOpen: false }),
  dismissWelcome: () => set({ welcomeDismissed: true }),
  reopenWelcome: () => set({ welcomeDismissed: false }),
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
  fetchReadiness: async () => {
    try {
      const prev = get().readiness;
      const next = await getReadiness();
      // Re-surface the tour whenever the app newly becomes not-ready (first boot,
      // or a key/model the user just removed or broke) — even if dismissed before.
      const becameNotReady = !next.ready && (prev === null || prev.ready);
      set(becameNotReady ? { readiness: next, welcomeDismissed: false } : { readiness: next });
    } catch {
      // Boot-time / transient backend errors must not crash the app; leave the
      // last known readiness in place (null = treated as not-ready by callers).
    }
  },
  fetchModelOptions: async () => {
    try {
      set({ modelOptions: await getModelOptions() });
    } catch {
      // Autocomplete is best-effort; keep any prior options on failure.
    }
  },
  save: async (patch) => {
    const res = await patchSettings(patch);
    set((s) => ({
      restartPending: Array.from(new Set([...s.restartPending, ...res.restart_required])),
    }));
    // Refresh masked values, the gate, and provider options (a new key may
    // unlock providers / flip readiness green).
    await get().fetchConfig();
    await Promise.all([get().fetchReadiness(), get().fetchModelOptions()]);
  },
}));
