/** Mark-block props, labels and the editor context the PaperHub blocks read. */
import { createContext, useContext } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import { useNotesStore } from "@/store/notes";
import type { MarkPiece } from "@/types/notes";

/** The note an editor shows, for blocks that link back to the paper. */
export interface NoteEditorContextValue {
  noteId: number;
  /** The paper of a paper note (marks live there); null for other notes. */
  paperContentId: number | null;
  paperTitle: string | null;
}

export const NoteEditorContext = createContext<NoteEditorContextValue>({
  noteId: 0,
  paperContentId: null,
  paperTitle: null,
});

export function useNoteEditorContext(): NoteEditorContextValue {
  return useContext(NoteEditorContext);
}

/** Props every paper reference block carries (BlockNote props are primitives,
 * so `pieces` and `source` travel as JSON strings). */
export const markPropSchema = {
  markId: { default: "" },
  color: { default: "" },
  kind: { default: "" },
  quote: { default: "" },
  pieces: { default: "[]" },
  source: { default: "{}" },
  lang: { default: "" },
  orig: { default: "" },
  sourceRef: { default: "" },
  translationChanged: { default: false },
  anchorLost: { default: false },
  piecesLost: { default: 0 },
} as const;

export interface MarkProps {
  markId: string;
  color: string;
  kind: string;
  quote: string;
  pieces: string;
  source: string;
  lang: string;
  orig: string;
  /** The translation block a mark on a translation was made on. */
  sourceRef: string;
  /** Its words are no longer in that translation (re-translated). */
  translationChanged: boolean;
  anchorLost: boolean;
  piecesLost: number;
}

export interface MarkSource {
  page?: number;
  section?: string;
}

function parse<T>(raw: string, fallback: T): T {
  try {
    return (JSON.parse(raw) as T | null) ?? fallback;
  } catch {
    return fallback;
  }
}

export function markPieces(p: MarkProps): MarkPiece[] {
  const v = parse<unknown>(p.pieces, []);
  return Array.isArray(v) ? (v as MarkPiece[]) : [];
}

export function markSource(p: MarkProps): MarkSource {
  return parse<MarkSource>(p.source, {});
}

/** "§2" for a numbered section key ("2 Method", "3.1 Setup"), else "§ Name". */
export function sectionLabel(key: string): string {
  const m = /^(\d+(?:\.\d+)*)\b/.exec(key.trim());
  return m ? `§${m[1]}` : `§ ${key.trim()}`;
}

/** "§2 · p. 3" (pages are 0-based in the data). */
export function whereLabel(src: MarkSource, t: TFunction): string {
  const parts: string[] = [];
  if (src.section) parts.push(sectionLabel(src.section));
  if (typeof src.page === "number") parts.push(t("notes:ref.page", "p. {{page}}", { page: src.page + 1 }));
  return parts.join(" · ");
}

/** Short names of the translation languages ("繁中"); literal by design. */
const LANG_SHORT: Record<string, string> = {
  "zh-TW": "繁中",
  "zh-CN": "简中",
  ja: "日本語",
  en: "English",
  ko: "한국어",
};

export function langShort(lang: string): string {
  return LANG_SHORT[lang] ?? lang;
}

const DEFAULT_COLOR_NAMES: Record<string, [string, string]> = {
  green: ["notes:color.green", "Key result"],
  blue: ["notes:color.blue", "Method"],
  pink: ["notes:color.pink", "Question"],
  purple: ["notes:color.purple", "Disagree"],
};

/** A colour's name as shown: the user's own, or the default name in the UI
 * language (the server seeds the English defaults). */
export function colorLabel(color: string, name: string | undefined, t: TFunction): string {
  const d = DEFAULT_COLOR_NAMES[color];
  if (name && (!d || name !== d[1])) return name;
  return d ? t(d[0], d[1]) : (name ?? "");
}

/** The user's name for a mark colour, else its default name. */
export function useColorName(color: string): string {
  const { t } = useTranslation();
  const custom = useNotesStore((s) => s.colorNames[color]);
  if (custom) return custom;
  const d = DEFAULT_COLOR_NAMES[color];
  return d ? t(d[0], d[1]) : "";
}
