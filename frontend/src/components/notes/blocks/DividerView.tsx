import { useEditorChange } from "@blocknote/react";
import { useMemo, useState } from "react";

import { useMarkNavStore } from "@/store/markNav";
import { useNotesStore } from "@/store/notes";

import { partsOfNote, type PartBlock } from "../parts";
import { useNoteEditorContext } from "./markProps";

type AnyEditor = NonNullable<Parameters<typeof useEditorChange>[1]>;

interface DocBlock extends PartBlock {
  children: DocBlock[];
}

/** The editor's blocks in document order (what the parts rule reads). */
function flatten(list: DocBlock[], out: PartBlock[] = []): PartBlock[] {
  for (const b of list) {
    out.push(b);
    flatten(b.children, out);
  }
  return out;
}

/** A divider's line: the rule, darkened when it frames the part you're in
 * (or, while the pointer is over a mark on the paper, the part quoting it).
 * A divider only bounds "one note" (parts.ts); it folds nothing (folds.ts).
 * The parts come from the editor's own document, so a divider just typed is
 * right before its sync reaches the store. */
export function DividerView({ id, editor }: { id: string; editor: AnyEditor }) {
  const { noteId } = useNoteEditorContext();
  const [blocks, setBlocks] = useState(() => flatten(editor.document as unknown as DocBlock[]));
  useEditorChange(() => setBlocks(flatten(editor.document)), editor);
  const active = useNotesStore((s) => s.activePart[noteId] ?? null);
  const hovered = useMarkNavStore((s) => s.hovered);

  const edge = useMemo(() => {
    const parts = partsOfNote(blocks);
    const hot = hovered === null ? -1 : parts.findIndex((p) => p.blocks.some((b) => b.props?.markId === hovered));
    const at = hot >= 0 ? hot : active === null ? -1 : parts.findIndex((p) => p.id === active);
    // The dividers just above and below the active part frame it.
    return at >= 0 && (parts[at]!.dividerId === id || parts[at + 1]?.dividerId === id);
  }, [blocks, active, hovered, id]);

  return (
    <div className="note-divider w-full" contentEditable={false} data-testid={`divider-${id}`}>
      <div className="flex h-[26px] items-center">
        <hr
          data-edge={edge ? "true" : undefined}
          className="note-divider-rule m-0 flex-1 border-0 border-t border-t-[color-mix(in_srgb,var(--foreground)_28%,transparent)] transition-[border-color,box-shadow] duration-200 ease-out motion-reduce:transition-none data-[edge=true]:border-t-[color-mix(in_srgb,var(--foreground)_70%,transparent)] data-[edge=true]:shadow-[0_1px_0_color-mix(in_srgb,var(--foreground)_35%,transparent)]"
        />
      </div>
    </div>
  );
}
