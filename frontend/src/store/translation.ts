import { create } from "zustand";
import { createJSONStorage, persist, type StateStorage } from "zustand/middleware";
import i18n, { LANGUAGE_ENDONYMS, type SupportedLanguage } from "@/lib/i18n";

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
}

export const useTranslationStore = create<TranslationState>()(
  persist(
    (set) => ({
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
