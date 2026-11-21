import { useEditorChange } from "@blocknote/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { noteOutline, type EditorBlockLike } from "@/lib/notes/outline";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";

import { useNoteEditorContext } from "./markProps";
import { TocList } from "./TocList";
import { useTocRows } from "./useTocRows";

export type TocEditor = NonNullable<Parameters<typeof useEditorChange>[1]>;

/** The note's table of contents: its headings and sections, listed live from
 * the editor's own document and indented by level; a click glides to the
 * entry. The block takes no text (atom caret like a divider). */
export function TocView({ editor }: { editor: TocEditor }) {
  const { t } = useTranslation();
  const { noteId } = useNoteEditorContext();
  const ref = useRef<HTMLDivElement>(null);
  const [outline, setOutline] = useState(() => noteOutline(editor.document as EditorBlockLike[]));
  useEditorChange(() => setOutline(noteOutline(editor.document as EditorBlockLike[])), editor);

  const entries = useTocRows(outline);

  const go = (blockId: string) => {
    const root = ref.current?.closest<HTMLElement>(".bn-container") ?? document.body;
    void revealBlock(root, blockId, { unfold: unfoldInNote(noteId) });
  };

  return (
    <div
      ref={ref}
      contentEditable={false}
      data-testid="toc-block"
      className="my-1 w-full select-none"
    >
      <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {t("notes:toc.title", "Table of contents")}
      </div>
      {entries.length === 0 ? (
        <div className="text-[13px] text-muted-foreground">
          {t("notes:toc.empty", "Headings you add appear here.")}
        </div>
      ) : (
        <TocList rows={entries} onGo={go} />
      )}
    </div>
  );
}
