import { create } from "zustand";

import { listSessionFeedback, putRunFeedback } from "@/lib/api";
import type { FeedbackPut, Polarity } from "@/types/feedback";

export interface RunVote {
  polarity: Polarity;
  reasons: string[];
  otherText: string | null;
  correction: string | null;
}

interface FeedbackState {
  /** Explicit feedback per answer, keyed by its run id. */
  byRun: Record<number, RunVote>;
  /** Merge a session's saved feedback (GET /feedback/runs?session_id=). */
  hydrate: (sessionId: number) => Promise<void>;
  /** Save one answer's feedback; optimistic, reverted if the PUT fails. */
  submit: (runId: number, body: FeedbackPut) => Promise<void>;
}

// Local edits are stamped so a hydrate response that left before an edit
// can't overwrite it when it lands.
let editSeq = 0;
const editedAt = new Map<number, number>();

/** Thumbs state for the chat. Not persisted: the backend is the source of truth. */
export const useFeedbackStore = create<FeedbackState>()((set, get) => ({
  byRun: {},
  hydrate: async (sessionId) => {
    const startedAt = editSeq;
    const rows = await listSessionFeedback(sessionId);
    set((s) => {
      const byRun = { ...s.byRun };
      for (const r of rows) {
        if ((editedAt.get(r.run_id) ?? 0) > startedAt) continue;
        byRun[r.run_id] = {
          polarity: r.polarity,
          reasons: r.reasons,
          otherText: r.other_text ?? null,
          correction: r.correction ?? null,
        };
      }
      return { byRun };
    });
  },
  submit: async (runId, body) => {
    editedAt.set(runId, ++editSeq);
    const prev = get().byRun[runId];
    const apply = (vote: RunVote | undefined) =>
      set((s) => {
        const byRun = { ...s.byRun };
        if (vote) byRun[runId] = vote;
        else delete byRun[runId];
        return { byRun };
      });
    apply(
      body.polarity
        ? {
            polarity: body.polarity,
            reasons: body.reasons,
            otherText: body.other_text ?? null,
            correction: body.correction ?? null,
          }
        : undefined,
    );
    try {
      await putRunFeedback(runId, body);
    } catch (err) {
      apply(prev);
      throw err;
    }
  },
}));
