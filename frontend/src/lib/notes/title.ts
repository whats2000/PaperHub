/** A note's name wherever it is named (lists, tabs, cards, the title's
 * placeholder): the user's title, else its default title (the paper / chat
 * / idea's title), else "Untitled". Mirrors the backend's `display_title`;
 * the stored title stays the user's text only. */
import i18n from "@/lib/i18n";

export interface TitledNote {
  note: { title: string };
  defaultTitle?: string;
}

/** The name a note shows when the user gave it none. */
export function defaultNoteTitle(n: TitledNote | undefined): string {
  return n?.defaultTitle?.trim() || i18n.t("notes:title.untitled", "Untitled");
}

export function noteDisplayTitle(n: TitledNote | undefined): string {
  return n?.note.title.trim() || defaultNoteTitle(n);
}
