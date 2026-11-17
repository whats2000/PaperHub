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
  /** Puts a paper's translation back as it was (`showMark`'s result). */
  restore: (prev: TranslationShown) => void;
  /** The reader has shown the mark: the request is done (a reader opened
   * later does not show it again). */
  revealed: (n: number) => void;
}

let revealCount = 0;

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
        set((s) => ({
          language: l,
          recent: [l, ...(Array.isArray(s.recent) ? s.recent : []).filter((r) => r !== l)].slice(0, MAX_RECENT),
        }));
      },
      toggle: (paperId) =>
        set((s) => ({
          enabledByPaper: {
            ...s.enabledByPaper,
            [paperId]: !s.enabledByPaper[paperId],
          },
        })),
      markHintSeen: () => set({ hintSeen: true }),
      reveal: null,
      showMark: (markId) => {
        const found = findMark(markId);
        const lang = found?.mark.lang;
        if (!found || !lang) return null;
        const { paperId } = found;
        const s = get();
        const prev: TranslationShown = { paperId, enabled: !!s.enabledByPaper[paperId], language: s.language };
        revealCount += 1;
        set({
          language: lang,
          enabledByPaper: { ...s.enabledByPaper, [paperId]: true },
          reveal: { paperId, markId, n: revealCount },
        });
        if (prev.enabled && prev.language === lang) return prev;
        // Nothing changes silently: say so, with the way back.
        const t = i18n.t.bind(i18n) as TFunction;
        toast(
          t("notes:marks.trTurnedOn", {
            defaultValue: "Translation turned on ({{lang}}) to show this mark",
            lang: langShort(lang),
          }),
          {
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
      revealed: (n) => set((s) => (s.reveal?.n === n ? { reveal: null } : s)),
      restore: (prev) =>
        set((s) => ({
          language: prev.language,
          enabledByPaper: { ...s.enabledByPaper, [prev.paperId]: prev.enabled },
        })),
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
