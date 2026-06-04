import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Composer } from "@/components/chat/Composer";
import { useChatStore } from "@/store/chat";

class FakeRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  onresult: ((e: { results: { 0: { transcript: string } }[] }) => void) | null = null;
  onerror: (() => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();
  constructor() {
    (window as unknown as { __lastRecognition?: FakeRecognition }).__lastRecognition = this;
  }
  emit(t: string) {
    this.onresult?.({ results: [{ 0: { transcript: t } }] });
  }
}

beforeEach(() => {
  useChatStore.setState({ composerDraft: "" });
});
afterEach(() => {
  delete (window as unknown as Record<string, unknown>).SpeechRecognition;
});

describe("Composer voice input", () => {
  it("hides the mic when Web Speech is unsupported", () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    expect(screen.queryByLabelText("Voice input")).not.toBeInTheDocument();
  });

  it("dictates interim transcript into the composer draft", () => {
    (window as unknown as Record<string, unknown>).SpeechRecognition =
      FakeRecognition;
    render(<Composer onSubmit={() => {}} disabled={false} />);
    const mic = screen.getByLabelText("Voice input");
    fireEvent.click(mic); // start
    const instance = (
      window as unknown as { __lastRecognition?: FakeRecognition }
    ).__lastRecognition;
    expect(instance).toBeDefined();
    // emit() fires onInterim outside React's event flow; act() flushes the
    // resulting state update so the textarea reflects it before we assert.
    act(() => instance!.emit("find the limitations"));
    expect(
      // eslint-disable-next-line @typescript-eslint/no-unnecessary-type-assertion
      (screen.getByLabelText("Message") as HTMLTextAreaElement).value,
    ).toContain("find the limitations");
  });
});
