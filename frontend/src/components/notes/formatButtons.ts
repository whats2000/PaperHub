/** What each formatting-bar button does, in the words of the shortcut it teaches. */
export type FormatId =
  | "bold"
  | "italic"
  | "underline"
  | "strike"
  | "quote"
  | "code"
  | "hidden"
  | "link"
  | "comment";

export const FORMAT_BUTTONS: { id: FormatId; name: string; markdown: string | null; key: string | null }[] = [
  { id: "bold", name: "Bold", markdown: "**text**", key: "Ctrl+B" },
  { id: "italic", name: "Italic", markdown: "*text* or _text_", key: "Ctrl+I" },
  { id: "underline", name: "Underline", markdown: "__text__", key: "Ctrl+U" },
  { id: "strike", name: "Strikethrough", markdown: "~~text~~", key: "Ctrl+Shift+S" },
  { id: "quote", name: "Turn into a quote", markdown: '" at the line start', key: null },
  { id: "code", name: "Inline code", markdown: "`text`", key: "Ctrl+E" },
  { id: "hidden", name: "Hide until clicked", markdown: "||text||", key: null },
  { id: "link", name: "Link a paper or section", markdown: "@", key: null },
  { id: "comment", name: "Comment", markdown: null, key: "Ctrl+Shift+M" },
];

/** The second tooltip line: the Markdown, then the key. */
export function formatHint(id: FormatId): string {
  const b = FORMAT_BUTTONS.find((x) => x.id === id)!;
  return [b.markdown, b.key].filter(Boolean).join(" · ");
}
