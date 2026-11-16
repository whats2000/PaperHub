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

export interface FormatButton {
  id: FormatId;
  name: string;
  /** Literal Markdown (never translated). */
  markdown: string | null;
  /** A second Markdown spelling, joined with the translated "or". */
  markdownAlt?: string;
  /** Prose after the Markdown, translated (`notes:format.<suffix>`). */
  suffix?: { key: string; en: string };
  /** The key (never translated). */
  key: string | null;
}

export const FORMAT_BUTTONS: FormatButton[] = [
  { id: "bold", name: "Bold", markdown: "**text**", key: "Ctrl+B" },
  { id: "italic", name: "Italic", markdown: "*text*", markdownAlt: "_text_", key: "Ctrl+I" },
  { id: "underline", name: "Underline", markdown: "__text__", key: "Ctrl+U" },
  { id: "strike", name: "Strikethrough", markdown: "~~text~~", key: "Ctrl+Shift+S" },
  {
    id: "quote",
    name: "Turn into a quote",
    markdown: '"',
    suffix: { key: "atLineStart", en: "at the line start" },
    key: null,
  },
  { id: "code", name: "Inline code", markdown: "`text`", key: "Ctrl+E" },
  { id: "hidden", name: "Hide until clicked", markdown: "||text||", key: null },
  { id: "link", name: "Link a paper or section", markdown: "@", key: null },
  { id: "comment", name: "Comment", markdown: null, key: "Ctrl+Shift+M" },
];

type Translate = (key: string, fallback: string) => string;

/** The second tooltip line: the Markdown, then the key. Only the joining
 * words are translated; Markdown and key names stay literal. */
export function formatHint(id: FormatId, tr: Translate = (_k, en) => en): string {
  const b = FORMAT_BUTTONS.find((x) => x.id === id)!;
  let md = b.markdown;
  if (md && b.markdownAlt) md += ` ${tr("notes:format.or", "or")} ${b.markdownAlt}`;
  if (md && b.suffix) md += ` ${tr(`notes:format.${b.suffix.key}`, b.suffix.en)}`;
  return [md, b.key].filter(Boolean).join(" · ");
}
