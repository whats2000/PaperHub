import type { TFunction } from "i18next";
import { toast } from "sonner";
import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import i18n, { LANGUAGE_ENDONYMS, type SupportedLanguage } from "@/lib/i18n";
import { langShort } from "@/components/notes/blocks/markProps";
import { useMarksStore } from "@/store/marks";

const MAX_RECENT = 5;

/** The UI locale's display name ("繁體中文", "English", ...). */
export function defaultLanguage(): string {
  const lng = i18n.language as string | undefined;
  if (lng && lng in LANGUAGE_ENDONYMS) {
    return LANGUAGE_ENDONYMS[lng as SupportedLanguage];
  }
  const base = lng?.split("-")[0];
  if (base && base in LANGUAGE_ENDONYMS) {
    return LANGUAGE_ENDONYMS[base as SupportedLanguage];
  }
  return LANGUAGE_ENDONYMS.en;
}

/** localStorage that never throws (private windows, blocked site data). */
const safeStorage: StateStorage = {
  getItem: (name) => {
    try {
      return localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    try {
      localStorage.setItem(name, value);
    } catch {
      /* ignore */
    }
  },
  removeItem: (name) => {
    try {
      localStorage.removeItem(name);
    } catch {
      /* ignore */
    }
  },
};

/** A paper's translation as the pill had it: on or off, and the language. */
export interface TranslationShown {
  paperId: number;
  enabled: boolean;
  language: string;
}

/** A request to the reader showing `paperId` to scroll to a mark and
 * flash it (`n` tells two requests for the same mark apart). */
export interface MarkReveal {
  paperId: number;
  markId: string;
  n: number;
}

interface TranslationState {
  /** Free-text target language. */
  language: string;
  /** Recently used languages, most recent first (max 5, trimmed, deduped). */
  recent: string[];
  enabledByPaper: Record<number, boolean>;
  /** The one-off PDF "hover a paragraph" hint has been shown. */
  hintSeen: boolean;
  setLanguage: (language: string) => void;
  toggle: (paperId: number) => void;
  markHintSeen: () => void;
  /** The mark the reader should scroll to and flash (see `showMark`). */
  reveal: MarkReveal | null;
  /** Shows a mark made on a translation: turns the paper's translation on
   * (or to the mark's language), asks the reader to scroll to the mark and
   * flash it, and — if that changed anything — toasts so with Turn off /
   * Back to <language>, which restores the previous state. Returns that
   * previous state (null: not a mark on a translation, or unknown). */
  showMark: (markId: string) => TranslationShown | null;
  /** Puts a paper's translation back as it was before `showMark` (its
   * result) — only while the translation is still as `showMark` left it;
   * once the user has changed it since, this does nothing. */
  restore: (prev: TranslationShown) => void;
  /** A reader has taken the request up (it is not dropped as unclaimed). */
  claimReveal: (n: number) => void;
  /** The reader has shown the mark (or given up): the request is done (a
   * reader opened later does not show it again). */
  revealed: (n: number) => void;
}

/** A request no reader takes up within this long is dropped: its paper
 * isn't open (and a reader opened much later must not jump to it). */
export const REVEAL_TTL_MS = 10_000;
/** The one "Translation turned on" toast: a newer one replaces it. */
const SHOW_MARK_TOAST = "tr-show-mark";

let revealCount = 0;
const claimed = new Set<number>();
/** What the open toast's way back restores, and the state `showMark` set
 * (the way back acts only while the translation is still that). */
let pending: { prev: TranslationShown; set: TranslationShown } | null = null;

const isShown = (
  s: { language: string; enabledByPaper: Record<number, boolean> },
  shown: TranslationShown,
) => !!s.enabledByPaper[shown.paperId] === shown.enabled && s.language === shown.language;

/** The user changed the translation themselves: the toast's way back is stale. */
function dropPending(): void {
  if (!pending) return;
  pending = null;
  toast.dismiss(SHOW_MARK_TOAST);
}

const withRecent = (recent: unknown, l: string) =>
  [l, ...(Array.isArray(recent) ? (recent as string[]) : []).filter((r) => r !== l)].slice(0, MAX_RECENT);

/** The paper and mark of an id, from the loaded marks. */
function findMark(markId: string) {
  for (const [pid, list] of Object.entries(useMarksStore.getState().marksByPaper)) {
    const m = list.find((x) => x.id === markId);
    if (m) return { paperId: Number(pid), mark: m };
  }
  return null;
}

export const useTranslationStore = create<TranslationState>()(
  persist(
    (set, get) => ({
      language: defaultLanguage(),
      recent: [],
      enabledByPaper: {},
      hintSeen: false,
      setLanguage: (language) => {
        const l = language.trim();
        if (!l) return;
        dropPending();
        set((s) => ({ language: l, recent: withRecent(s.recent, l) }));
      },
      toggle: (paperId) => {
        dropPending();
        set((s) => ({
          enabledByPaper: {
            ...s.enabledByPaper,
            [paperId]: !s.enabledByPaper[paperId],
          },
        }));
      },
      markHintSeen: () => set({ hintSeen: true }),
      reveal: null,
      showMark: (markId) => {
        const found = findMark(markId);
        const lang = found?.mark.lang;
        if (!found || !lang) return null;
        const { paperId } = found;
        const s = get();
        const now: TranslationShown = { paperId, enabled: !!s.enabledByPaper[paperId], language: s.language };
        // A second request while the first's toast still holds: its way back
        // is still the state before the first.
        const prev = pending && pending.set.paperId === paperId && isShown(s, pending.set) ? pending.prev : now;
        revealCount += 1;
        const n = revealCount;
        set({
          language: lang,
          recent: withRecent(s.recent, lang),
          enabledByPaper: { ...s.enabledByPaper, [paperId]: true },
          reveal: { paperId, markId, n },
        });
        setTimeout(() => {
          if (!claimed.has(n) && get().reveal?.n === n) set({ reveal: null });
        }, REVEAL_TTL_MS);
        if (prev.enabled && prev.language === lang) {
          dropPending();
          return now;
        }
        pending = { prev, set: { paperId, enabled: true, language: lang } };
        // Nothing changes silently: say so, with the way back.
        const t = i18n.t.bind(i18n) as TFunction;
        toast(
          t("notes:marks.trTurnedOn", {
            defaultValue: "Translation turned on ({{lang}}) to show this mark",
            lang: langShort(lang),
          }),
          {
            id: SHOW_MARK_TOAST,
            action: {
              label: prev.enabled
                ? t("notes:marks.trBackTo", { defaultValue: "Back to {{lang}}", lang: langShort(prev.language) })
                : t("notes:marks.trTurnOff", "Turn off"),
              onClick: () => get().restore(prev),
            },
          },
        );
        return prev;
      },
      claimReveal: (n) => {
        claimed.add(n);
      },
      revealed: (n) => {
        claimed.delete(n);
        set((s) => (s.reveal?.n === n ? { reveal: null } : s));
      },
      restore: (prev) => {
        if (!pending || pending.prev !== prev || !isShown(get(), pending.set)) return;
        pending = null;
        set((s) => ({
          language: prev.language,
          enabledByPaper: { ...s.enabledByPaper, [prev.paperId]: prev.enabled },
        }));
      },
    }),
    {
      name: "paperhub-translation-v1",
      storage: createJSONStorage(() => safeStorage),
      partialize: (s) => ({
        language: s.language,
        recent: s.recent,
        enabledByPaper: s.enabledByPaper,
        hintSeen: s.hintSeen,
      }),
    },
  ),
);
