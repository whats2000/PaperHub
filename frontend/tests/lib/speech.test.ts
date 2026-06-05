import { afterEach, describe, expect, it, vi } from "vitest";
import { createSpeechRecognizer, isSpeechSupported } from "@/lib/speech";

interface FakeResult {
  0: { transcript: string };
}

class FakeRecognition {
  lang = "";
  continuous = false;
  interimResults = false;
  onresult: ((e: { results: FakeResult[] }) => void) | null = null;
  onerror: ((e: { error: string }) => void) | null = null;
  onend: (() => void) | null = null;
  start = vi.fn();
  stop = vi.fn();
  emit(transcripts: string[]) {
    this.onresult?.({
      results: transcripts.map((t): FakeResult => ({ 0: { transcript: t } })),
    });
  }
}

afterEach(() => {
  delete (window as unknown as Record<string, unknown>).SpeechRecognition;
  delete (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
});

describe("speech", () => {
  it("isSpeechSupported reflects the API presence", () => {
    expect(isSpeechSupported()).toBe(false);
    (window as unknown as Record<string, unknown>).SpeechRecognition =
      FakeRecognition;
    expect(isSpeechSupported()).toBe(true);
  });

  it("returns null when unsupported", () => {
    expect(createSpeechRecognizer({ onInterim: () => {} })).toBeNull();
  });

  it("feeds concatenated transcript to onInterim and starts/stops", () => {
    (window as unknown as Record<string, unknown>).SpeechRecognition =
      FakeRecognition;
    const seen: string[] = [];
    const rec = createSpeechRecognizer({ onInterim: (t) => seen.push(t) });
    expect(rec).not.toBeNull();
    rec!.start();
    const instance = (rec as unknown as { _raw: FakeRecognition })._raw;
    instance.emit(["hello ", "world"]);
    expect(seen).toContain("hello world");
    rec!.stop();
    expect(instance.stop).toHaveBeenCalled();
  });
});
