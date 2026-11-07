import { create } from "zustand";

export type Page = "chat" | "ideas" | "feedback";

interface NavState {
  page: Page;
  /** Items awaiting the user's confirmation; set by the Feedback page (H2). */
  feedbackBadge: number;
  setPage: (p: Page) => void;
  setFeedbackBadge: (n: number) => void;
}

/** Which main-pane page is showing. Not persisted: the app always opens on chat. */
export const useNavStore = create<NavState>()((set) => ({
  page: "chat",
  feedbackBadge: 0,
  setPage: (page) => set({ page }),
  setFeedbackBadge: (feedbackBadge) => set({ feedbackBadge }),
}));
