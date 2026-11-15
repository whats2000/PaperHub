import { create } from "zustand";

export type Page = "chat" | "ideas" | "feedback";

/** The fields of a pending held search the nav counts are built from. */
export interface HeldRef {
  session_id: number | null;
  unattended: boolean;
}

interface NavState {
  page: Page;
  /** Items awaiting the user's confirmation; set by the Feedback page (H2). */
  feedbackBadge: number;
  /** Pending held searches (H5a); the sidebar entry shows only above 0. */
  heldBadge: number;
  /** Pending held searches decided in a chat, per backend session id. */
  heldBySession: Record<number, number>;
  /** H5a: Ask-first actions paused runs wait on, per backend session id
   *  (a chat row's marker counts them with its held searches). */
  pausedActionsBySession: Record<number, number>;
  /** Bumped by `refreshHeld()`; `useHeldBadge` refetches on every change. */
  heldRefreshSeq: number;
  /** A waiting card (held search id or action id) to scroll to, ring and
   *  focus once it is mounted. */
  focusHeldId: string | null;
  setPage: (p: Page) => void;
  setFeedbackBadge: (n: number) => void;
  setHeldBadge: (n: number) => void;
  /** Set the badge and the per-chat counts from the pending held list. */
  setHeldList: (rows: HeldRef[]) => void;
  /** Set the per-chat paused-action counts from GET /chat/paused. */
  setPausedList: (rows: PausedRef[]) => void;
  refreshHeld: () => void;
  setFocusHeldId: (id: string | null) => void;
}

/** The fields of a paused run's card the per-chat counts are built from. */
export interface PausedRef {
  session_id: number;
  /** `search`: an Ask-first search card (H5a), counted like an action. */
  kind: "held" | "action" | "search";
}

/** Per-chat counts: only searches decided in their chat, not background ones. */
export function heldCountsBySession(rows: HeldRef[]): Record<number, number> {
  const out: Record<number, number> = {};
  for (const r of rows) {
    if (r.session_id == null || r.unattended) continue;
    out[r.session_id] = (out[r.session_id] ?? 0) + 1;
  }
  return out;
}

/** Which main-pane page is showing. Not persisted: the app always opens on chat. */
export const useNavStore = create<NavState>()((set) => ({
  page: "chat",
  feedbackBadge: 0,
  heldBadge: 0,
  heldBySession: {},
  pausedActionsBySession: {},
  heldRefreshSeq: 0,
  focusHeldId: null,
  setPage: (page) => set({ page }),
  setFeedbackBadge: (feedbackBadge) => set({ feedbackBadge }),
  setHeldBadge: (heldBadge) => set({ heldBadge }),
  setHeldList: (rows) =>
    set({ heldBadge: rows.length, heldBySession: heldCountsBySession(rows) }),
  setPausedList: (rows) => {
    const out: Record<number, number> = {};
    // Held rows are already counted from the held list.
    for (const r of rows) if (r.kind !== "held") out[r.session_id] = (out[r.session_id] ?? 0) + 1;
    set({ pausedActionsBySession: out });
  },
  refreshHeld: () => set((s) => ({ heldRefreshSeq: s.heldRefreshSeq + 1 })),
  setFocusHeldId: (focusHeldId) => set({ focusHeldId }),
}));
