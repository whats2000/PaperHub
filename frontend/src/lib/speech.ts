export interface SpeechRecognizer {
  start: () => void;
  stop: () => void;
  /** The underlying recognition instance — exposed for tests only. */
  _raw: unknown;
}

interface Handlers {
  /** Full transcript (interim + final) accumulated so far this session. */
  onInterim: (text: string) => void;
  onError?: (error: string) => void;
  onEnd?: () => void;
}

interface MinimalRecognition {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { results: ArrayLike<{ 0: { transcript: string } }> }) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
}

type RecognitionCtor = new () => MinimalRecognition;

function getCtor(): RecognitionCtor | null {
  const w = window as unknown as {
    SpeechRecognition?: RecognitionCtor;
    webkitSpeechRecognition?: RecognitionCtor;
  };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function isSpeechSupported(): boolean {
  return getCtor() !== null;
}

/** Create a recognizer, or null if the browser has no Web Speech API.
 *  Continuous (manual stop, not silence-gated); interim results stream so the
 *  composer fills live as you speak.
 *
 *  IMPORTANT: `onInterim` fires with the FULL accumulated transcript for the
 *  current session on every result event — NOT a delta. Consumers must capture
 *  a base string at start() time and REPLACE (base + transcript) on each call,
 *  never append, or the text double-accumulates. */
export function createSpeechRecognizer(
  handlers: Handlers,
): SpeechRecognizer | null {
  const Ctor = getCtor();
  if (!Ctor) return null;
  const rec = new Ctor();
  rec.lang = navigator.language || "en-US";
  rec.continuous = true;
  rec.interimResults = true;
  rec.onresult = (e) => {
    let text = "";
    for (let i = 0; i < e.results.length; i++) {
      text += e.results[i]![0].transcript;
    }
    handlers.onInterim(text);
  };
  rec.onerror = (e) => handlers.onError?.(String(e.error ?? "speech-error"));
  rec.onend = () => handlers.onEnd?.();
  return {
    start: () => rec.start(),
    stop: () => rec.stop(),
    _raw: rec,
  };
}
