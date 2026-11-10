import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ConversationContext } from "@/components/feedback/ConversationContext";
import type { QueueItem } from "@/types/feedback";

const item = (answer: string): QueueItem => ({
  feedback_id: 1,
  run_id: 101,
  session_id: 1,
  session_title: "Session",
  papers: [],
  earlier: [],
  question: "Question?",
  answer,
  flagged: "",
  next_reply: null,
  reviewer_doubt: "",
  guessed_reasons: [],
});

describe("ConversationContext citations", () => {
  it.each(["[chunk:101,102]", "[chunk:75143, 75161]", "[chunk:7]", "[3]", "[unsourced:not_read]"])(
    "renders %s as a superscript, not raw text",
    (marker) => {
      const { container } = render(<ConversationContext item={item(`Alpha ${marker} beta.`)} />);
      const sups = Array.from(container.querySelectorAll("sup")).map((s) => s.textContent);
      expect(sups).toEqual([marker]);
      expect(screen.getByText(/Alpha/)).toBeInTheDocument();
    },
  );
});
