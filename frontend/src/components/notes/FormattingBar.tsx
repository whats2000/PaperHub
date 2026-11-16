/** The notes formatting bar: B I U S · quote, code, hide · Link, Comment.
 *
 * Every tooltip names the Markdown and the key, so the bar teaches the
 * shortcuts. The Comment button is present but disabled until the comments
 * feature wires `onComment` (Ctrl+Shift+M belongs to it as well).
 */
import { SuggestionMenu } from "@blocknote/core/extensions";
import {
  FormattingToolbar,
  FormattingToolbarController,
  useBlockNoteEditor,
  useComponentsContext,
  useEditorState,
} from "@blocknote/react";
import { AtSign, Bold, Code, EyeOff, Italic, MessageSquare, Quote, Strikethrough, Underline } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

import type { noteSchema } from "./schema";
import { FORMAT_BUTTONS, formatHint, type FormatId } from "./formatButtons";

const ICONS: Record<FormatId, ReactNode> = {
  bold: <Bold className="size-4" />,
  italic: <Italic className="size-4" />,
  underline: <Underline className="size-4" />,
  strike: <Strikethrough className="size-4" />,
  quote: <Quote className="size-4" />,
  code: <Code className="size-4" />,
  hidden: <EyeOff className="size-4" />,
  link: <AtSign className="size-4" />,
  comment: <MessageSquare className="size-4" />,
};

type NoteBlockSchema = typeof noteSchema.blockSchema;
type NoteInlineSchema = typeof noteSchema.inlineContentSchema;
type NoteStyleSchema = typeof noteSchema.styleSchema;
type StyleId = "bold" | "italic" | "underline" | "strike" | "code" | "hidden";

function Separator() {
  return <span aria-hidden className="mx-0.5 w-px self-stretch bg-border" />;
}

function Bar({ onComment }: { onComment?: () => void }) {
  const { t } = useTranslation();
  const C = useComponentsContext()!;
  const editor = useBlockNoteEditor<NoteBlockSchema, NoteInlineSchema, NoteStyleSchema>();
  const state = useEditorState({
    editor,
    selector: ({ editor: e }) => {
      if (!e.isEditable) return undefined;
      const styles = e.getActiveStyles() as Record<string, unknown>;
      const blocks = e.getSelection()?.blocks ?? [e.getTextCursorPosition().block];
      return {
        bold: !!styles.bold,
        italic: !!styles.italic,
        underline: !!styles.underline,
        strike: !!styles.strike,
        code: !!styles.code,
        hidden: !!styles.hidden,
        quote: blocks.length > 0 && blocks.every((b: { type: string }) => b.type === "quote"),
      };
    },
  });
  if (!state) return null;

  const button = (
    id: FormatId,
    props: { onClick?: () => void; isSelected?: boolean; isDisabled?: boolean; children?: ReactNode },
  ) => {
    const name = t(`notes:format.${id}`, FORMAT_BUTTONS.find((b) => b.id === id)!.name);
    return (
      <C.FormattingToolbar.Button
        key={id}
        className="bn-button"
        data-test={id}
        label={name}
        mainTooltip={name}
        secondaryTooltip={formatHint(id)}
        icon={ICONS[id]}
        {...props}
      />
    );
  };
  const toggle = (id: StyleId) =>
    button(id, {
      isSelected: state[id],
      onClick: () => {
        editor.focus();
        editor.toggleStyles({ [id]: true });
      },
    });

  return (
    <FormattingToolbar>
      {toggle("bold")}
      {toggle("italic")}
      {toggle("underline")}
      {toggle("strike")}
      <Separator />
      {button("quote", {
        isSelected: state.quote,
        onClick: () => {
          editor.focus();
          const blocks = editor.getSelection()?.blocks ?? [editor.getTextCursorPosition().block];
          for (const b of blocks) editor.updateBlock(b, { type: state.quote ? "paragraph" : "quote" });
        },
      })}
      {toggle("code")}
      {toggle("hidden")}
      <Separator />
      {button("link", {
        onClick: () => {
          editor.focus();
          editor.getExtension(SuggestionMenu)?.openSuggestionMenu("@");
        },
        children: <span className="text-[13px]">{t("notes:format.linkLabel", "Link")}</span>,
      })}
      {button("comment", {
        isDisabled: !onComment,
        onClick: onComment,
        children: <span className="text-[13px]">{t("notes:format.commentLabel", "Comment")}</span>,
      })}
    </FormattingToolbar>
  );
}

/** Mounts the bar in place of BlockNote's default (pass `formattingToolbar={false}`
 * to the view). */
export function NoteFormattingBar({ onComment }: { onComment?: () => void }) {
  return <FormattingToolbarController formattingToolbar={() => <Bar onComment={onComment} />} />;
}
