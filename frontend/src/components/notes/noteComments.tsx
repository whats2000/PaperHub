/** Comments in the notes editor: the key that starts one, the anchor style on
 * the words, and the controller that shows a thread under them.
 *
 * A new comment is only a remembered range until it is posted; posting makes
 * the thread on the server, then puts its id on the words as the inline
 * `comment` style. So Esc on an empty new comment leaves nothing behind.
 */
import { createExtension, type BlockNoteEditor } from "@blocknote/core";
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { useNotesStore } from "@/store/notes";
import type { NoteComment } from "@/types/notes";

import { runThreadAction } from "./commentActions";
import { CommentPopover, CommentThread } from "./CommentThread";

// The comment code works on any schema; it reads block ids and content only.
type Ed = BlockNoteEditor<any, any, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

/** Fired (bubbling) from the editor when Ctrl/Cmd+Shift+M is pressed; the
 * comment controller of that editor starts a comment. */
export const START_COMMENT_EVENT = "paperhub:start-comment";

/** Ctrl/Cmd+Shift+M starts a comment on the selected words. */
export const commentKeys = createExtension({
  key: "paperhubCommentKeys",
  keyboardShortcuts: {
    "Mod-Shift-m": ({ editor }) => {
      editor.domElement?.dispatchEvent(new CustomEvent(START_COMMENT_EVENT, { bubbles: true }));
      return true;
    },
  },
});

// ---- the anchor style ----------------------------------------------------------

type Run = { type: string; styles?: Record<string, unknown>; content?: Run[] } & Record<string, unknown>;

/** `content` without comment `id` on any run; null when nothing carried it. */
function without(content: Run[], id: string): Run[] | null {
  let changed = false;
  const out = content.map((run) => {
    let next = run;
    if (run.styles && run.styles.comment === id) {
      const { comment: _gone, ...rest } = run.styles;
      void _gone;
      next = { ...run, styles: rest };
      changed = true;
    }
    if (Array.isArray(run.content)) {
      const inner = without(run.content, id);
      if (inner) {
        next = { ...next, content: inner };
        changed = true;
      }
    }
    return next;
  });
  return changed ? out : null;
}

/** Takes comment `id` off the words (in whichever blocks carry it). */
export function stripCommentStyle(editor: Ed, id: string): void {
  const walk = (blocks: { id: string; content?: unknown; children?: unknown[] }[]) => {
    for (const b of blocks) {
      if (Array.isArray(b.content)) {
        const next = without(b.content as Run[], id);
        if (next) editor.updateBlock(b.id, { content: next } as never);
      }
      if (b.children?.length) walk(b.children as never);
    }
  };
  walk(editor.document as never);
}

/** Puts comment `id` on `quote` in block `blockId`'s own text (not its child
 * blocks), as the text reads NOW so edits made since are kept: the occurrence
 * nearest document position `near`, else the first. False when the quote or
 * the block is gone: the thread is then open without an underline. */
export function applyCommentStyle(
  editor: Ed,
  id: string,
  blockId: string,
  quote: string,
  near?: number,
): boolean {
  const view = editor._tiptapEditor.view;
  const mark = view.state.schema.marks.comment;
  if (!mark || !quote) return false;
  let found = false;
  view.state.doc.descendants((node, pos) => {
    if (found) return false;
    if (node.attrs.id !== blockId) return true;
    // A block is its container: the inline content first, child blocks after.
    const content = node.firstChild;
    if (!content) return false;
    const start = pos + 2; // inside the container, inside the content
    // The text with the document position of each character (anything that is
    // not text, e.g. a mention chip, takes one placeholder character).
    let text = "";
    const at: number[] = [];
    content.descendants((child, off) => {
      if (child.isText) {
        for (let k = 0; k < child.text!.length; k++) {
          at.push(start + off + k);
          text += child.text![k];
        }
      } else if (child.isLeaf) {
        at.push(start + off);
        text += "\ufffc";
      }
      return true;
    });
    let best = -1;
    for (let k = text.indexOf(quote); k >= 0; k = text.indexOf(quote, k + 1)) {
      if (best < 0 || (near !== undefined && Math.abs(at[k]! - near) < Math.abs(at[best]! - near))) best = k;
      if (near === undefined) break;
    }
    if (best >= 0) {
      const from = at[best]!;
      const to = at[best + quote.length - 1]! + 1;
      view.dispatch(view.state.tr.addMark(from, to, mark.create({ stringValue: id })));
      found = true;
    }
    return false;
  });
  return found;
}

/** Where comment `id`'s words start in the document now (undefined when no
 * text carries it): the hint that puts it back on the same occurrence of a
 * repeated quote. */
export function commentAnchor(editor: Ed, id: string): number | undefined {
  let at: number | undefined;
  editor._tiptapEditor.view.state.doc.descendants((node, pos) => {
    if (at !== undefined) return false;
    if (node.isText && node.marks.some((m) => m.type.name === "comment" && m.attrs.stringValue === id)) {
      at = pos;
      return false;
    }
    return true;
  });
  return at;
}

/** A thread to put back over its quote after its block was rewritten. */
export interface ReanchorHint {
  id: string;
  blockId: string;
  quote: string;
  near: number | undefined;
}

// ---- the controller --------------------------------------------------------------

interface Draft {
  blockId: string;
  quote: string;
  from: number;
  to: number;
}

type Shown = { kind: "draft"; draft: Draft } | { kind: "thread"; id: string };

type Anchor = { getBoundingClientRect: () => DOMRect };

function rectAt(editor: Ed, pos: number): DOMRect {
  try {
    const c = editor._tiptapEditor.view.coordsAtPos(pos);
    return new DOMRect(c.left, c.top, 0, c.bottom - c.top);
  } catch {
    // The view is gone or has no layout: sit on the editor itself.
    return editor._tiptapEditor.view.dom.getBoundingClientRect();
  }
}

export function useNoteComments(editor: Ed, noteId: number) {
  const { t } = useTranslation();
  const comments = useNotesStore((s) => s.notes[noteId]?.comments);
  const addComment = useNotesStore((s) => s.addComment);
  const replyComment = useNotesStore((s) => s.replyComment);
  const setCommentStatus = useNotesStore((s) => s.setCommentStatus);

  // `shown` stays while the popover animates out; `open` drives the animation.
  const [shown, setShown] = useState<Shown | null>(null);
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<Anchor | null>(null);

  const show = useCallback((s: Shown, a: Anchor) => {
    setShown(s);
    setAnchor(a);
    setOpen(true);
  }, []);

  /** Start a comment on the selected words (Ctrl+Shift+M, the bar's button). */
  const start = useCallback(() => {
    const view = editor._tiptapEditor.view;
    const sel = view.state.selection;
    if (sel.empty || !sel.$from.parent.isTextblock) return;
    // One block only: a selection across blocks comments its first block.
    const from = sel.from;
    const to = Math.min(sel.to, sel.$from.end());
    const quote = view.state.doc.textBetween(from, to, " ").trim();
    if (!quote) return;
    // getSelection() only answers for a selection over several blocks.
    const blockId = editor.getSelection()?.blocks[0]?.id ?? editor.getTextCursorPosition().block.id;
    show({ kind: "draft", draft: { blockId, quote, from, to } }, { getBoundingClientRect: () => rectAt(editor, to) });
  }, [editor, show]);

  // Ctrl+Shift+M (see commentKeys) arrives as an event from this editor.
  useEffect(() => {
    const onKey = (e: Event) => {
      if (editor.domElement?.contains(e.target as Node)) start();
    };
    document.addEventListener(START_COMMENT_EVENT, onKey);
    return () => document.removeEventListener(START_COMMENT_EVENT, onKey);
  }, [editor, start]);

  const thread: NoteComment | null =
    shown?.kind === "thread" ? (comments?.find((c) => c.id === shown.id) ?? null) : null;

  // The words of the open thread are marked.
  useEffect(() => {
    if (!open || shown?.kind !== "thread") return;
    const els = [...document.querySelectorAll(`.note-comment[data-comment-id="${CSS.escape(shown.id)}"]`)];
    els.forEach((el) => el.setAttribute("data-on", ""));
    return () => els.forEach((el) => el.removeAttribute("data-on"));
  }, [open, shown]);

  /** A click on commented words opens its thread. */
  const onEditorClick = useCallback(
    (e: React.MouseEvent) => {
      const el = (e.target as HTMLElement).closest?.(".note-comment[data-comment-id]");
      if (!el || !window.getSelection()?.isCollapsed) return;
      const id = el.getAttribute("data-comment-id") ?? "";
      if (!comments?.some((c) => c.id === id && c.status === "open")) return;
      show({ kind: "thread", id }, el);
    },
    [comments, show],
  );

  const close = () => setOpen(false);

  /** Puts threads back over their quotes in rewritten text (an accepted
   * suggestion). A thread whose quote is gone stays open and is shown, so
   * the user sees that its text changed. */
  const reanchor = useCallback(
    (hints: ReanchorHint[]) => {
      let lost: ReanchorHint | null = null;
      for (const h of hints) {
        if (!applyCommentStyle(editor, h.id, h.blockId, h.quote, h.near)) lost ??= h;
      }
      if (!lost) return;
      const sel = `.bn-block-outer[data-id="${CSS.escape(lost.blockId)}"]`;
      const el = editor.domElement?.querySelector(sel) ?? editor.domElement;
      if (el) show({ kind: "thread", id: lost.id }, el);
    },
    [editor, show],
  );

  const post = async (text: string) => {
    if (!shown) return;
    if (shown.kind === "draft") {
      const { draft } = shown;
      const created = await addComment(noteId, draft.blockId, draft.quote, text);
      // The document may have changed while the thread was being saved: find
      // the words again rather than trusting the positions from before.
      applyCommentStyle(editor, created.id, draft.blockId, draft.quote, draft.from);
      setShown({ kind: "thread", id: created.id });
    } else {
      await replyComment(noteId, shown.id, text);
    }
  };

  const act = (kind: "resolve" | "delete") => {
    if (shown?.kind !== "thread") return;
    const id = shown.id;
    const blockId = thread?.block_id ?? "";
    const quote = thread?.quote ?? "";
    // Where its words were, so Undo marks the same occurrence of a quote
    // that appears more than once.
    let near: number | undefined;
    close();
    runThreadAction({
      kind,
      strip: () => {
        near = commentAnchor(editor, id) ?? near;
        stripCommentStyle(editor, id);
      },
      restore: () => void applyCommentStyle(editor, id, blockId, quote, near),
      setStatus: (status) => setCommentStatus(noteId, id, status),
      t: (k, en) => t(k, en),
    });
  };

  const popover = (
    <CommentPopover open={open && shown !== null} anchor={anchor} onOpenChange={setOpen}>
      {shown && (
        <CommentThread
          key={shown.kind === "draft" ? "draft" : shown.id}
          thread={thread}
          quote={shown.kind === "draft" ? shown.draft.quote : (thread?.quote ?? "")}
          onPost={post}
          onResolve={() => act("resolve")}
          onDelete={() => act("delete")}
          onClose={close}
          onDiscard={close}
          textChanged={thread?.status === "open" && commentAnchor(editor, thread.id) === undefined}
        />
      )}
    </CommentPopover>
  );

  return { start, onEditorClick, popover, reanchor };
}
