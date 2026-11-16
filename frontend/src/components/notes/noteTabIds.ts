/** The ids tying a note tab to the note it shows (`idBase` from `useId`). */
import type { NoteTab } from "./NotesTabs";

export const noteTabId = (idBase: string, tab: NoteTab) => `${idBase}-tab-${tab}`;
export const notePanelId = (idBase: string) => `${idBase}-panel`;
