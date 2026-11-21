import { useEditorChange } from "@blocknote/react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { noteOutline, type EditorBlockLike } from "@/lib/notes/outline";
import { revealBlock, unfoldInNote } from "@/lib/notes/reveal";

import { useNoteEditorContext } from "./markProps";
import { useTocRows } from "./useTocRows";

export type TocEditor = NonNullable<Parameters<typeof useEditorChange>[1]>;

const INDENT_PX = 16;

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
        <ul className="m-0 list-none p-0">
          {entries.map((e) => (
            <li
              key={e.blockId}
              data-state={e.state}
              className="grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none"
              style={{
                gridTemplateRows: e.state === "open" ? "1fr" : "0fr",
                opacity: e.state === "open" ? 1 : 0,
              }}
            >
              <div
                className="min-h-0 overflow-hidden"
                style={{ paddingLeft: (e.level - 1) * INDENT_PX }}
              >
                <button
                  type="button"
                  tabIndex={e.state === "out" ? -1 : 0}
                  onClick={() => go(e.blockId)}
                  className="w-full truncate rounded-sm py-[2px] text-left text-[13px] text-muted-foreground underline-offset-2 transition-colors duration-150 hover:bg-accent hover:text-foreground hover:underline motion-reduce:transition-none"
                >
                  {e.text}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
