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

import { flushPendingThreadActions, runThreadAction } from "./commentActions";
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

/** Takes comment `id` off the words (in whichever blocks carry it); returns
 * what puts it back. */
export function stripCommentStyle(editor: Ed, id: string): () => void {
  const before: { blockId: string; content: unknown }[] = [];
  const walk = (blocks: { id: string; content?: unknown; children?: unknown[] }[]) => {
    for (const b of blocks) {
      if (Array.isArray(b.content)) {
        const next = without(b.content as Run[], id);
        if (next) {
          before.push({ blockId: b.id, content: b.content });
          editor.updateBlock(b.id, { content: next } as never);
        }
      }
      if (b.children?.length) walk(b.children as never);
    }
  };
  walk(editor.document as never);
  return () => {
    for (const { blockId, content } of before) {
      if (editor.getBlock(blockId)) editor.updateBlock(blockId, { content } as never);
    }
  };
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

  // Tell the server about a delete still on its toast when the editor goes.
  useEffect(() => () => flushPendingThreadActions(), []);

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

  const post = async (text: string) => {
    if (!shown) return;
    if (shown.kind === "draft") {
      const { draft } = shown;
      const created = await addComment(noteId, draft.blockId, draft.quote, text);
      const view = editor._tiptapEditor.view;
      const mark = view.state.schema.marks.comment;
      const size = view.state.doc.content.size;
      if (mark && draft.to <= size) {
        view.dispatch(view.state.tr.addMark(draft.from, draft.to, mark.create({ stringValue: created.id })));
      }
      setShown({ kind: "thread", id: created.id });
    } else {
      await replyComment(noteId, shown.id, text);
    }
  };

  const act = (kind: "resolve" | "delete") => {
    if (shown?.kind !== "thread") return;
    const id = shown.id;
    close();
    runThreadAction({
      kind,
      strip: () => stripCommentStyle(editor, id),
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
        />
      )}
    </CommentPopover>
  );

  return { start, onEditorClick, popover };
}
