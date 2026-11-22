/** The fold controls of a note's editor (folds.ts): each foldable block's
 * toggle, hanging in the left gutter right against its text (Notion's
 * toggle heading), and a folded block's "… 4 blocks · 2 marks" chip after
 * its text. Both are rendered into the hosts the part decorations place
 * (partsDecorations.ts), so the block's text never moves.
 *
 * The toggle shows while the block is the side menu's (the pointer is on
 * it), while it has keyboard focus, and always while folded; on a bullet
 * item it stands where the bullet is (gutter.css hides the bullet then).
 */
import { SideMenuExtension } from "@blocknote/core/extensions";
import { useEditorChange, useExtensionState } from "@blocknote/react";
import { ChevronRight } from "lucide-react";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import { useNotesStore } from "@/store/notes";

import { useNoteEditorContext } from "./blocks/markProps";
import { foldShortcut } from "./foldKeys";
import { usePresence } from "./foldMotion";
import { foldOwners, foldSummaryOf, type FoldNode } from "./folds";
import { foldLine } from "./parts";
import { refreshPartDecorations, type FoldHosts } from "./partsDecorations";

type AnyEditor = NonNullable<Parameters<typeof useEditorChange>[1]>;

/** A block's fold toggle: ▸ folded, ⌄ open (it turns in 200 ms). */
export function FoldToggle({ folded, hovered, onToggle }: { folded: boolean; hovered: boolean; onToggle: () => void }) {
  const { t } = useTranslation();
  const label = folded ? t("notes:fold.unfold", "Unfold") : t("notes:fold.fold", "Fold the blocks under this");
  const title = folded
    ? t("notes:fold.unfoldTitle", "Unfold · {{shortcut}}", { shortcut: foldShortcut(true) })
    : t("notes:fold.foldTitle", "Fold · {{shortcut}}", { shortcut: foldShortcut(false) });
  return (
    <button
      type="button"
      className="note-fold-toggle note-gutter-btn"
      aria-expanded={!folded}
      aria-label={label}
      title={title}
      data-hover={hovered ? "true" : undefined}
      // The caret stays where it is.
      onMouseDown={(e) => e.preventDefault()}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
    >
      <ChevronRight aria-hidden />
    </button>
  );
}

/** A folded block's chip: what it hides; a click unfolds. */
export function FoldChip({ summary, state, onUnfold }: { summary: { blocks: number; marks: number }; state: "open" | "closed"; onUnfold: () => void }) {
  const { t } = useTranslation();
  return (
    <button
      type="button"
      className="note-fold-chip"
      data-state={state}
      tabIndex={state === "open" ? 0 : -1}
      title={t("notes:fold.unfold", "Unfold")}
      onMouseDown={(e) => e.preventDefault()}
      onClick={(e) => {
        e.stopPropagation();
        onUnfold();
      }}
    >
      {`… ${foldLine(t, summary)}`}
    </button>
  );
}

/** The toggles and chips of one editor (the notes pane; the note card
 * never folds). */
export function FoldControls({ editor, noteId, hosts }: { editor: AnyEditor; noteId: number; hosts: FoldHosts }) {
  const [tree, setTree] = useState(() => editor.document as unknown as FoldNode[]);
  useEditorChange(() => setTree(editor.document), editor);
  const foldedIds = useNotesStore((s) => s.folded[noteId]);
  const toggleFold = useNotesStore((s) => s.toggleFold);
  const hovered = useExtensionState(SideMenuExtension, {
    editor: editor as never,
    selector: (s) => (s?.show ? s.block.id : null),
  });

  const owners = useMemo(() => {
    const ranges = foldOwners(tree);
    const types = new Map<string, string>();
    const walk = (list: FoldNode[]) =>
      list.forEach((b) => {
        types.set(b.id, b.type);
        walk(b.children);
      });
    walk(tree);
    return [...ranges].map(([id, range]) => ({ id, type: types.get(id) ?? "", summary: foldSummaryOf(range) }));
  }, [tree]);
  const folded = useMemo(() => new Set(foldedIds ?? []), [foldedIds]);

  // A chip just unfolded fades out while the decorations still place it
  // (FoldHosts.chips); once gone they are redrawn without it.
  useEffect(() => {
    hosts.setOnExpire(() => refreshPartDecorations(editor._tiptapEditor.view));
    return () => hosts.setOnExpire(undefined);
  }, [hosts, editor]);

  const out: ReactNode[] = [];
  for (const o of owners) {
    const isFolded = folded.has(o.id);
    const toggle = () => toggleFold(noteId, o.id);
    out.push(
      createPortal(
        <FoldToggle folded={isFolded} hovered={hovered === o.id} onToggle={toggle} />,
        hosts.toggle(o.id),
        `t:${o.id}`,
      ),
    );
    // Every chip stays rendered (into its host, placed only while folded or
    // fading out), so an unfold fades it in place. A § section draws its
    // own chip (it has no text to follow).
    if (o.type !== "section") {
      out.push(
        createPortal(
          <FoldChip summary={o.summary} state={isFolded ? "open" : "closed"} onUnfold={toggle} />,
          hosts.chip(o.id),
          `c:${o.id}`,
        ),
      );
    }
  }
  return <>{out}</>;
}

/** A folded § section's chip after its title (a section folds like an H1,
 * to the next section or H1); it fades out when unfolded. */
export function SectionFoldChip({ id, editor }: { id: string; editor: AnyEditor }) {
  const { noteId } = useNoteEditorContext();
  const folded = useNotesStore((s) => (s.folded[noteId] ?? []).includes(id));
  const toggleFold = useNotesStore((s) => s.toggleFold);
  const { mounted, state } = usePresence(folded);
  const [tree, setTree] = useState(() => editor.document as unknown as FoldNode[]);
  useEditorChange(() => setTree(editor.document), editor);
  const range = useMemo(() => (mounted ? foldOwners(tree).get(id) : undefined), [mounted, tree, id]);
  if (!mounted || !range) return null;
  return <FoldChip summary={foldSummaryOf(range)} state={state} onUnfold={() => toggleFold(noteId, id)} />;
}
