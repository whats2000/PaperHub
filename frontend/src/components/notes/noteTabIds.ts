/** The ids tying a note tab to the note it shows (`idBase` from `useId`;
 * `key`: "chat", or "paper-<id>" for a paper's tab). */
export const noteTabId = (idBase: string, key: string) => `${idBase}-tab-${key}`;
export const notePanelId = (idBase: string) => `${idBase}-panel`;
