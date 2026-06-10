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
import { hasBlockingConfigIssue } from "../lib/readiness";

// Once a config is verified good, remember it so we don't re-ping the provider
// on every boot/reload (a real LLM call — costs latency + risks a transient
// failure). Settings-open and save still re-verify, so the model staying valid
// is re-confirmed whenever the user actually touches config.
const READY_CACHE_KEY = "paperhub-config-verified";
const READY_CACHE_TTL_MS = 7 * 24 * 60 * 60 * 1000; // 7 days

function readReadinessCache(): boolean {
  try {
    const raw = localStorage.getItem(READY_CACHE_KEY);
    if (!raw) return false;
    const at = Number((JSON.parse(raw) as { at?: unknown }).at);
    return Number.isFinite(at) && Date.now() - at < READY_CACHE_TTL_MS;
  } catch {
    return false;
  }
}

function writeReadinessCache(ready: boolean): void {
  try {
    if (ready) localStorage.setItem(READY_CACHE_KEY, JSON.stringify({ at: Date.now() }));
    else localStorage.removeItem(READY_CACHE_KEY);
  } catch {
    // storage disabled (private mode) — caching is best-effort
  }
}

// A minimal "ready" state used when we trust the verified cache and skip the
// live ping; the Settings panel fetches real per-slot details on open.
const READY_FROM_CACHE: SettingsReadiness = {
  ready: true,
  credentials_set: true,
  models: {
    small: { model: "", key_ok: true, missing_keys: [], error: null, detail: null },
    flagship: { model: "", key_ok: true, missing_keys: [], error: null, detail: null },
  },
};

interface SettingsState {
  isOpen: boolean;
  config: SettingsConfig | null;
  loading: boolean;
  error: boolean;
  restartPending: string[];
  /** First-run gate state; null until first fetch. */
  readiness: SettingsReadiness | null;
  /** True while a readiness pre-flight is in progress (the ping takes a few
   *  seconds) — the UI shows "checking…" instead of a stale warning. */
  readinessChecking: boolean;
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
  /** Boot-time gate: use the verified cache if fresh, else ping live. */
  ensureReadiness: () => Promise<void>;
  /** Live re-verify (Settings open / after save) — always pings. */
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
  readinessChecking: false,
  modelOptions: null,
  welcomeDismissed: false,
  open: () => {
    set({ isOpen: true });
    if (!get().config) void get().fetchConfig();
    void get().fetchModelOptions();
    // Opening Settings is the moment to show real, fresh per-slot status.
    void get().fetchReadiness();
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
  ensureReadiness: async () => {
    // Skip the live ping if we verified a good config recently — the model list
    // changes slowly, and re-pinging every boot wastes a call and risks a
    // transient false-lock. A stale/absent cache falls through to a live check.
    if (get().readiness != null) return;
    if (readReadinessCache()) {
      set({ readiness: READY_FROM_CACHE });
      return;
    }
    await get().fetchReadiness();
  },
  fetchReadiness: async () => {
    set({ readinessChecking: true });
    try {
      const next = await getReadiness();
      // Remember a verified-good config; clear the cache only on a DEFINITIVE
      // failure (missing/rejected key) so a transient blip doesn't force a
      // re-ping storm. The tour stays a first-entry affair (in-memory dismiss).
      if (next.ready) writeReadinessCache(true);
      else if (hasBlockingConfigIssue(next)) writeReadinessCache(false);
      set({ readiness: next });
    } catch {
      // Boot-time / transient backend errors must not crash the app; leave the
      // last known readiness in place (null = treated as not-ready by callers).
    } finally {
      set({ readinessChecking: false });
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
