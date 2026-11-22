/** The notes editor: BlockNote with PaperHub's blocks, synced block by block.
 *
 * Each change is diffed against the last synced document by stable block id
 * (`sync.diffDocuments`) and queued in the notes store, which sends one burst
 * of ops per pause in typing. Conflicted blocks come back through the store's
 * `reloads` and are replaced in place, so the caret elsewhere is kept.
 */
import { insertOrUpdateBlockForSlashMenu } from "@blocknote/core";
import { SuggestionMenu } from "@blocknote/core/extensions";
import { en, ja, zh, zhTW } from "@blocknote/core/locales";
import {
  getDefaultReactSlashMenuItems,
  SuggestionMenuController,
  useCreateBlockNote,
  type DefaultReactSuggestionItem,
  type FloatingUIOptions,
} from "@blocknote/react";
import { BlockNoteView } from "@blocknote/shadcn";
import { TextSelection } from "@tiptap/pm/state";
import "@blocknote/shadcn/style.css";
import { syntaxHighlighter } from "@blocknote/code-block";
import {
  AtSign,
  CloudOff,
  FileText,
  Hash,
  Heading,
  Lightbulb,
  ListTree,
  MessageSquare,
  NotebookPen,
  Sigma,
  TextQuote,
} from "lucide-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { listLibrary } from "@/lib/api";
import { runsCite } from "@/lib/marks/cites";
import { prefersReducedMotion } from "@/lib/motion";
import { propText } from "@/lib/notes/ops";
import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/store/canvas";
import { savedNoteId, useNotesStore } from "@/store/notes";

import { atomCaret, isOpeningMove } from "./atomCaret";
import { insertInlineMath } from "./blocks/InlineMath";
import { textRange } from "./blocks/MarkCite";
import { turnBlockIntoCite } from "./blocks/citeForms";
import { NoteEditorContext, sectionLabel } from "./blocks/markProps";
import { citationChips } from "./citationDecorations";
import { EditorTip } from "./EditorTip";
import { insertMention, parseMentionHref, type MentionTarget } from "./formatActions";
import { LinkPickerMenu } from "./LinkPickerMenu";
import {
  completeLinkQuery,
  insertNoteLink,
  linkItems,
  opensLinkPicker,
  reopenLinkPicker,
  type LinkItem,
} from "./linkPicker";
import { NoteFormattingBar } from "./FormattingBar";
import { lostCommentBadges, useBadgeRefresh, useLostCommentBadges } from "./commentBadges";
import { LostCommentBadges } from "./LostCommentBadges";
import { commentKeys, useNoteComments, type ReanchorHint } from "./noteComments";
import { noteSuggestionMarks, useNoteSuggestions, useSuggestionHosts } from "./noteSuggestions";
import { codeFence, dividerRule, inlineMarks } from "./inlineMarks";
import { isFindKey, noteFind } from "./noteFind";
import { NoteFindBar } from "./NoteFindBar";
import { caretFoldOwner, foldOwners } from "./folds";
import { FoldControls } from "./FoldControls";
import "./gutter.css";
import { NoteSideMenu } from "./NoteSideMenu";
import { partOf } from "./parts";
import {
  createFoldHosts,
  noteParts,
  refreshPartDecorations,
  type FoldHosts,
  type PartsView,
} from "./partsDecorations";
import { NoteTitle } from "./NoteTitle";
import { MATH_ALIASES, mathInput, slashLineText, turnIntoEquation } from "./mathInput";
import { noteSchema, paperhubKeys } from "./schema";
import { rankSlashItems } from "./slashRank";
import { NoteTableHandles } from "./NoteTableHandles";
import { TableCaptions } from "./TableCaption";
import { createCaptionHosts, tableCaptions } from "./tableCaptions";
import { insertNoteTable, notePasteHandler, tableClean, tableKeys } from "./tableKeys";
import { tableBorderView } from "./tableBorderView";
import { tableMotion } from "./tableMotion";
import { UnsavedDrafts } from "./UnsavedDrafts";
import {
  diffDocuments,
  pruneUnsavedEmpty,
  rekeyEditorBlocks,
  rekeyTree,
  toEditorBlocks,
  treeIds,
  type EditorBlock,
} from "./sync";

const LINK_ICONS = {
  paperNote: NotebookPen,
  chatNote: MessageSquare,
  ideaNote: Lightbulb,
  paper: FileText,
  heading: Heading,
  section: Hash,
} as const;

export interface NoteEditorProps {
  noteId: number;
  /** "pane" fills the Notes pane; "card" is the compact note card. */
  variant: "pane" | "card";
  /** The block holding the caret, as it moves. */
  onBlockFocus?: (blockId: string) => void;
  /** Opens the shortcuts guide (the tip's "All shortcuts"). */
  onOpenGuide?: () => void;
  /** The note card: the one note of the section shown (a part id; the rest
   * of the note is hidden), the mark clicked (its reference is tinted) and
   * where another reference's Show ↗ goes. */
  card?: { partId: string; markId: string; onJump: (markId: string) => void };
  /** Where the find strip goes (the pane's, under its toolbar); none: at
   * the top of this editor. */
  findHost?: HTMLElement | null;
  /** Bumped to open find (the toolbar's Find, Ctrl+F in the pane); the
   * value seen at mount opens nothing. */
  findRequest?: number;
}

const DICTIONARIES = { en, ja, "zh-TW": zhTW, "zh-CN": zh } as const;

/** What the part decorations draw for a note (Focus fades, folds; a chip
 * fading out after its unfold stays drawn until it is gone). */
function partsViewOf(noteId: number, hosts: FoldHosts): PartsView {
  const s = useNotesStore.getState();
  const folded = new Set(s.folded[noteId] ?? []);
  return {
    focusOn: s.focusOn,
    activePart: s.activePart[noteId] ?? null,
    folded,
    chips: hosts.chips(folded),
  };
}

function dictionaryFor(lang: string) {
  return DICTIONARIES[lang as keyof typeof DICTIONARIES] ?? (lang.startsWith("zh") ? zh : en);
}

/** What `takeBlocks` uses of the editor. */
interface BlockOps {
  getBlock: (id: string) => { id: string } | undefined;
  getPrevBlock: (id: string) => { id: string } | undefined;
  getNextBlock: (id: string) => { id: string } | undefined;
  removeBlocks: (ids: string[]) => unknown;
  updateBlock: (id: string, update: never) => unknown;
  insertBlocks: (blocks: never, ref: string, placement: "before" | "after") => unknown;
}

/** Puts `ids` as they are in `tree` into the editor: changed, moved, added
 * or removed, each in place. False when one can't be placed (the caller
 * then replaces the whole document). */
function takeBlocks(editor: BlockOps, tree: EditorBlock[], ids: readonly string[]): boolean {
  const where = new Map<string, { block: EditorBlock; siblings: EditorBlock[]; index: number }>();
  const walk = (list: EditorBlock[]) =>
    list.forEach((b, index) => {
      where.set(b.id, { block: b, siblings: list, index });
      walk(b.children);
    });
  walk(tree);
  for (const id of ids) {
    const want = where.get(id);
    const have = editor.getBlock(id);
    if (!want) {
      if (have) editor.removeBlocks([id]);
      continue;
    }
    const prev = want.siblings[want.index - 1];
    const next = want.siblings[want.index + 1];
    const inPlace =
      have &&
      (prev ? editor.getPrevBlock(id)?.id === prev.id : !editor.getPrevBlock(id)) &&
      (!next || editor.getNextBlock(id)?.id === next.id || !editor.getBlock(next.id));
    if (have && inPlace) {
      editor.updateBlock(id, { type: want.block.type, props: want.block.props, content: want.block.content } as never);
      continue;
    }
    if (have) editor.removeBlocks([id]);
    if (prev && editor.getBlock(prev.id)) editor.insertBlocks([want.block] as never, prev.id, "after");
    else if (next && editor.getBlock(next.id)) editor.insertBlocks([want.block] as never, next.id, "before");
    else return false;
  }
  return true;
}

/** The editor instance a note keeps: a note saved from a placeholder
 * (its first write) keeps the placeholder's editor, so typing goes on. */
function useEditorKey(noteId: number): number {
  return useNotesStore((s) => s.origins[noteId] ?? noteId);
}

export function NoteEditor(props: NoteEditorProps) {
  // Each note gets its own loading line and editor state.
  return <NoteEditorFrame key={useEditorKey(props.noteId)} {...props} />;
}

function NoteEditorFrame(props: NoteEditorProps) {
  const { t, i18n } = useTranslation();
  const editorKey = useEditorKey(props.noteId);
  const loaded = useNotesStore((s) => s.notes[props.noteId] !== undefined);
  // The loading line fades out over the editor fading in (never vanishes).
  const [loaderGone, setLoaderGone] = useState(loaded);
  const showLoader = !loaderGone && !(loaded && prefersReducedMotion());
  return (
    <div className={cn("relative min-w-0", props.variant === "pane" && "h-full")}>
      {showLoader && (
        <div
          role="status"
          data-closed={loaded ? "" : undefined}
          className={cn(
            "px-4 py-3 text-sm text-muted-foreground transition-opacity duration-200 ease-out animate-in fade-in-0 data-closed:opacity-0 motion-reduce:animate-none motion-reduce:transition-none",
            loaded && "pointer-events-none absolute inset-x-0 top-0",
          )}
          onTransitionEnd={() => {
            if (loaded) setLoaderGone(true);
          }}
        >
          {t("notes:loading", "Loading the note…")}
        </div>
      )}
      {/* A new note, or a new UI language (BlockNote's own strings), is a new
          editor: its history and dictionary start fresh. */}
      {loaded && <LoadedNoteEditor key={`${editorKey}:${i18n.language}`} {...props} />}
    </div>
  );
}

function LoadedNoteEditor({
  noteId,
  variant,
  onBlockFocus,
  onOpenGuide,
  card,
  findHost = null,
  findRequest = 0,
}: NoteEditorProps) {
  const { t, i18n } = useTranslation();
  const { resolvedTheme } = useTheme();
  const note = useNotesStore((s) => s.notes[noteId]!.note);
  const applyLocal = useNotesStore((s) => s.applyLocal);
  const setQuoteTarget = useNotesStore((s) => s.setQuoteTarget);
  const reload = useNotesStore((s) => s.reloads[noteId]);
  const retrying = useNotesStore((s) => s.retrying[noteId] ?? false);
  const focusOn = useNotesStore((s) => s.focusOn);
  const tableGuides = useNotesStore((s) => s.tableGuides);
  const activePart = useNotesStore((s) => s.activePart[noteId] ?? null);
  const folded = useNotesStore((s) => s.folded[noteId]);
  const setActivePart = useNotesStore((s) => s.setActivePart);
  // The bars of the agent's suggestions are rendered into these hosts.
  const suggestionHost = useSuggestionHosts();
  // Badges for open comment threads whose words are gone.
  const lostBadges = useLostCommentBadges(noteId);
  // Tables' captions and notes are rendered into these hosts.
  const [captionHosts] = useState(createCaptionHosts);
  // Fold toggles and chips are rendered into these hosts.
  const [foldHosts] = useState(createFoldHosts);
  // The note card shows one part, and nothing else.
  const cardPart = card?.partId ?? null;
  // The editor outlives a placeholder's save (its id changes): what it
  // reads at run time follows the saved id.
  const editorKey = useEditorKey(noteId);

  const editor = useCreateBlockNote(
    {
      schema: noteSchema,
      initialContent: (() => {
        const tree = toEditorBlocks(useNotesStore.getState().notes[noteId]!.blocks);
        return tree.length ? (tree as never) : undefined;
      })(),
      dictionary: dictionaryFor(i18n.language),
      // A pasted table keeps only what the notes store (tableKeys.ts).
      pasteHandler: notePasteHandler as never,
      // Merged cells (the cell menu's Merge / Split); never a cell colour.
      tables: { splitCells: true, cellBackgroundColor: false, cellTextColor: false, headers: false },
      extensions: [
        paperhubKeys,
        tableKeys,
        tableClean,
        tableMotion,
        tableBorderView,
        mathInput,
        atomCaret,
        commentKeys,
        inlineMarks,
        codeFence,
        dividerRule,
        syntaxHighlighter,
        noteParts(() => partsViewOf(savedNoteId(editorKey), foldHosts), cardPart, foldHosts),
        noteSuggestionMarks(() => ({
          suggestions: useNotesStore.getState().notes[savedNoteId(editorKey)]?.suggestions ?? [],
          host: suggestionHost,
        })),
        lostCommentBadges(lostBadges.badgeView),
        tableCaptions(captionHosts),
        citationChips(),
        noteFind(),
      ],
    },
    [editorKey],
  );

  // What the server has (or will have once the queue lands), as editor blocks.
  const synced = useRef<EditorBlock[] | null>(null);
  const applyingRemote = useRef(false);
  const doc = useCallback(() => editor.document as unknown as EditorBlock[], [editor]);
  if (synced.current === null) {
    const known = treeIds(toEditorBlocks(useNotesStore.getState().notes[noteId]!.blocks));
    synced.current = pruneUnsavedEmpty(doc(), known);
  }

  // A fold outlives its block (or what it folded) only as a stale id: forget it.
  const keepFolds = useCallback(() => {
    if (!(useNotesStore.getState().folded[noteId] ?? []).length) return;
    useNotesStore.getState().keepFolds(noteId, new Set(foldOwners(doc()).keys()));
  }, [doc, noteId]);

  // This editor, among the editors open on the note (the card and the pane).
  const origin = useId();
  const onChange = useCallback(() => {
    keepFolds();
    if (applyingRemote.current || synced.current === null) return;
    const next = pruneUnsavedEmpty(doc(), treeIds(synced.current));
    const ops = diffDocuments(synced.current, next);
    synced.current = next;
    if (ops.length) applyLocal(noteId, ops, origin);
  }, [applyLocal, doc, noteId, keepFolds, origin]);

  // Another editor open on this note (the note card while the pane is open
  // behind it, or the other way round) changed blocks: they are taken here
  // in place, from the shared store, so this editor never sends an older
  // copy of them back. What was typed here first goes out as usual, and
  // this editor's undo history stays.
  useEffect(
    () =>
      useNotesStore.subscribe((s, prev) => {
        const e = s.localEdits[noteId];
        if (!e || e === prev.localEdits[noteId] || e.origin === origin) return;
        onChange();
        const tree = toEditorBlocks(useNotesStore.getState().notes[noteId]?.blocks ?? []);
        applyingRemote.current = true;
        try {
          if (!takeBlocks(editor, tree, e.blockIds)) {
            editor.replaceBlocks(editor.document, (tree.length ? tree : [{ type: "paragraph" }]) as never);
          }
        } finally {
          applyingRemote.current = false;
        }
        synced.current = pruneUnsavedEmpty(doc(), new Set([...treeIds(synced.current ?? []), ...treeIds(tree)]));
      }),
    [editor, noteId, origin, onChange, doc],
  );

  // Blocks the server saved under a new id (pasted from another note, they
  // kept that note's id): re-keyed in place, so the next edit finds them.
  useEffect(
    () =>
      useNotesStore.subscribe((s, prev) => {
        const r = s.renames[noteId];
        if (!r || r === prev.renames[noteId]) return;
        const ids = new Map(r.pairs);
        applyingRemote.current = true;
        try {
          rekeyEditorBlocks(editor._tiptapEditor.view as never, ids);
        } finally {
          applyingRemote.current = false;
        }
        synced.current = rekeyTree(synced.current ?? [], ids);
      }),
    [editor, noteId],
  );

  const comments = useNoteComments(editor, noteId);
  // Comment threads to put back over their quotes once an accepted
  // suggestion's text shows (see useNoteSuggestions).
  const pendingReanchorRef = useRef<ReanchorHint[] | null>(null);
  const suggestionBars = useNoteSuggestions(editor, noteId, suggestionHost, pendingReanchorRef);
  const { reanchor } = comments;
  const threads = useNotesStore((s) => s.notes[noteId]?.comments);
  useBadgeRefresh(editor, [threads, lostBadges.badges]);

  // Blocks the server replaced (a conflict) or a whole reload: apply them in
  // place without sending them back as edits.
  const seenSeq = useRef(reload?.seq ?? 0);
  useEffect(() => {
    if (!reload || reload.seq === seenSeq.current) return;
    seenSeq.current = reload.seq;
    const blocks = useNotesStore.getState().notes[noteId]?.blocks ?? [];
    const tree = toEditorBlocks(blocks);
    const flat = new Map<string, EditorBlock>();
    const walk = (list: EditorBlock[]) =>
      list.forEach((b) => {
        flat.set(b.id, b);
        walk(b.children);
      });
    walk(tree);
    applyingRemote.current = true;
    try {
      const full =
        reload.blockIds === null ||
        reload.blockIds.some((id) => flat.has(id) && !editor.getBlock(id));
      if (full) {
        editor.replaceBlocks(editor.document, (tree.length ? tree : [{ type: "paragraph" }]) as never);
      } else {
        for (const id of reload.blockIds ?? []) {
          const want = flat.get(id);
          if (!editor.getBlock(id)) continue;
          if (want) {
            editor.updateBlock(id, {
              type: want.type,
              props: want.props,
              content: want.content,
            } as never);
          } else {
            editor.removeBlocks([id]);
          }
        }
      }
    } finally {
      applyingRemote.current = false;
    }
    synced.current = pruneUnsavedEmpty(doc(), new Set([...treeIds(synced.current ?? []), ...flat.keys()]));
    // After an accepted suggestion: the threads' styles go back on as the
    // user's edit of the block (synced like typing).
    const hints = pendingReanchorRef.current;
    if (hints) {
      pendingReanchorRef.current = null;
      if (hints.length) reanchor(hints);
    }
  }, [reload, editor, noteId, doc, reanchor]);

  // "Add note" on a reader selection (the pane) or a highlight (the note
  // card): the caret goes on a line after the new reference, ready to type
  // (once a reload has brought the block in). Left empty, the line is never
  // sent (`pruneUnsavedEmpty`).
  const caretAfter = useNotesStore((s) => (s.caretAfter?.noteId === noteId ? s.caretAfter : null));
  const setCaretAfter = useNotesStore((s) => s.setCaretAfter);
  useEffect(() => {
    if (!caretAfter || (caretAfter.in ?? "pane") !== variant) return;
    const block = editor.getBlock(caretAfter.blockId);
    if (!block) return;
    // Right after a new inline citation: once the reload has brought it in.
    if (caretAfter.markId && !runsCite(block.content, caretAfter.markId)) return;
    setCaretAfter(null);
    if (caretAfter.offset !== undefined) {
      // Right after a new inline citation, in its paragraph.
      const view = editor._tiptapEditor.view;
      const range = textRange(view, block.id);
      if (!range) return;
      const at = range.start + Math.max(0, Math.min(caretAfter.offset, range.size));
      view.dispatch(view.state.tr.setSelection(TextSelection.create(view.state.doc, at)).scrollIntoView());
      editor.focus();
      return;
    }
    const next = editor.getNextBlock(block);
    const emptyLine =
      next?.type === "paragraph" && Array.isArray(next.content) && next.content.length === 0 ? next : null;
    const line = emptyLine ?? editor.insertBlocks([{ type: "paragraph" }] as never, block, "after")[0];
    if (!line) return;
    editor.setTextCursorPosition(line, "start");
    editor.focus();
  }, [caretAfter, editor, reload, variant, setCaretAfter]);

  // The part decorations read the store; redraw them when it changes.
  useEffect(() => {
    try {
      refreshPartDecorations(editor._tiptapEditor.view, variant === "card" ? cardPart : undefined);
    } catch {
      /* not mounted yet: the decorations draw on mount */
    }
  }, [editor, focusOn, activePart, folded, cardPart, variant]);

  const onSelectionChange = useCallback(() => {
    // Opening the note moved its selection off a first block that takes no
    // text: not a caret of the user's.
    if (isOpeningMove(editor._tiptapEditor.view)) return;
    let blockId: string;
    try {
      blockId = editor.getTextCursorPosition().block.id;
    } catch {
      return; // no caret (e.g. the editor lost focus)
    }
    onBlockFocus?.(blockId);
    // A new mark goes right after the block the caret was last in.
    // (and an inline citation at the caret's place in its text).
    const { $from } = editor._tiptapEditor.view.state.selection;
    const inText = $from.parent.isTextblock && $from.node($from.depth - 1)?.type.name === "blockContainer";
    useNotesStore.getState().setLastCaret(noteId, blockId, inText ? $from.parentOffset : null);
    // The caret's part is the one you're in (a click on a faded part moves
    // Focus there). Parts are read from the editor, ahead of the sync.
    const flat: EditorBlock[] = [];
    const walk = (list: EditorBlock[]) =>
      list.forEach((b) => {
        flat.push(b);
        walk(b.children);
      });
    walk(doc());
    const part = partOf(flat, blockId)?.id ?? null;
    if (part !== null && part !== useNotesStore.getState().activePart[noteId]) setActivePart(noteId, part);
  }, [editor, onBlockFocus, doc, noteId, setActivePart]);

  const slashItems = useCallback(
    (query: string): Promise<DefaultReactSuggestionItem[]> => {
      const defaults = getDefaultReactSlashMenuItems(editor);
      // Callout and Equation join BlockNote's own basic group, right after
      // its last item: a group shown twice is two headings (and one React key).
      const basicGroup =
        defaults.find((i) => (i as { key?: string }).key === "paragraph")?.group ??
        t("notes:slash.groupBasic", "Basic blocks");
      const basicEnd = defaults.map((i) => i.group).lastIndexOf(basicGroup) + 1;
      const paperhub = t("notes:slash.groupPaperHub", "PaperHub");
      const basicItems: DefaultReactSuggestionItem[] = [
        {
          title: t("notes:slash.callout", "Callout"),
          subtext: t("notes:slash.calloutHint", "A tinted box that stands out"),
          aliases: ["callout", "tip", "note"],
          group: basicGroup,
          icon: <Lightbulb className="size-[18px]" />,
          onItemClick: () => insertOrUpdateBlockForSlashMenu(editor, { type: "callout" } as never),
        },
        {
          title: t("notes:slash.equation", "Block equation"),
          subtext: t("notes:slash.equationHint", "LaTeX on its own line · $$ + Enter"),
          aliases: [...MATH_ALIASES, "block", "$$"],
          group: basicGroup,
          icon: <Sigma className="size-[18px]" />,
          onItemClick: () => insertOrUpdateBlockForSlashMenu(editor, { type: "math" } as never),
        },
        {
          title: t("notes:slash.inlineEquation", "Inline equation"),
          subtext: t("notes:slash.inlineEquationHint", "LaTeX inside the sentence · $x$"),
          aliases: [...MATH_ALIASES, "inline", "$"],
          group: basicGroup,
          icon: <Sigma className="size-[18px]" />,
          onItemClick: () => insertInlineMath(editor),
        },
        {
          title: t("notes:slash.toc", "Table of contents"),
          subtext: t("notes:slash.tocHint", "Lists the headings of this note; click to jump"),
          aliases: ["toc", "contents", "outline", "table of contents", "目錄", "目录", "目次"],
          group: basicGroup,
          icon: <ListTree className="size-[18px]" />,
          onItemClick: () => insertOrUpdateBlockForSlashMenu(editor, { type: "toc" } as never),
        },
      ];
      // Notion's "Turn into": the line's text (without the "/query" being
      // typed) becomes the equation's LaTeX.
      const lineText = slashLineText(editor, query);
      if (lineText.trim()) {
        basicItems.push({
          title: t("notes:slash.turnIntoEquation", "Block equation · Turn into"),
          subtext: t("notes:slash.turnIntoEquationHint", "This line's text becomes the LaTeX"),
          aliases: [...MATH_ALIASES, "turn", "convert"],
          group: basicGroup,
          icon: <Sigma className="size-[18px]" />,
          onItemClick: () => turnIntoEquation(editor),
        });
      }
      // BlockNote's table item inserts 3 × 2 with no header; Notion's 3 × 3
      // has its first row a header.
      const withTable = defaults.map((i) =>
        (i as { key?: string }).key === "table"
          ? {
              ...i,
              title: t("notes:slash.table", "Table"),
              subtext: t("notes:slash.tableHint", "3 × 3, first row a header · paste one to bring it in"),
              aliases: ["table", "表格", "表", "grid"],
              onItemClick: () => insertNoteTable(editor),
            }
          : i,
      );
      const items: DefaultReactSuggestionItem[] = [
        ...withTable.slice(0, basicEnd),
        ...basicItems,
        ...withTable.slice(basicEnd),
        {
          title: t("notes:slash.quotePaper", "Quote from the paper"),
          subtext: t("notes:slash.quotePaperHint", "Select text in the reader; it lands here."),
          aliases: ["quote", "paper", "mark", "highlight"],
          group: paperhub,
          icon: <TextQuote className="size-[18px]" />,
          onItemClick: () => {
            const block = editor.getTextCursorPosition().block;
            // An empty line the server has not seen yet cannot be a hint;
            // the quote then lands after the block above it.
            const known = treeIds(synced.current ?? []);
            const hint = known.has(block.id) ? block : editor.getPrevBlock(block);
            setQuoteTarget({ noteId, blockId: (hint ?? block).id });
            toast(t("notes:slash.quotePaperHint", "Select text in the reader; it lands here."));
          },
        },
        {
          title: t("notes:slash.link", "Link a paper or section"),
          subtext: t("notes:slash.linkHint", "Same as typing @."),
          aliases: ["link", "mention", "@"],
          group: paperhub,
          icon: <AtSign className="size-[18px]" />,
          onItemClick: () => editor.getExtension(SuggestionMenu)?.openSuggestionMenu("@"),
        },
      ];
      return Promise.resolve(rankSlashItems(items, query));
    },
    [editor, noteId, setQuoteTarget, t],
  );

  const paperContentId = note.target_kind === "paper" ? Number(note.target_id) : null;

  const mentionItems = useCallback(
    async (query: string): Promise<DefaultReactSuggestionItem[]> => {
      const insert = (props: MentionTarget) => () => insertMention(editor, props);
      const q = query.trim().toLowerCase();
      const sections: DefaultReactSuggestionItem[] =
        paperContentId === null
          ? []
          : (useNotesStore.getState().notes[noteId]?.blocks ?? [])
              .filter((b) => b.type === "section" && b.section_key)
              .filter((b) => !q || (b.section_key ?? "").toLowerCase().includes(q))
              .map((b) => ({
                title: propText(b.props.title) || (b.section_key ?? ""),
                subtext: sectionLabel(b.section_key ?? ""),
                group: t("notes:mention.sections", "Sections of this paper"),
                icon: <Hash className="size-[18px]" />,
                onItemClick: insert({
                  kind: "section",
                  paperId: paperContentId,
                  sectionKey: b.section_key ?? "",
                  label: propText(b.props.title) || (b.section_key ?? ""),
                }),
              }));
      let papers: DefaultReactSuggestionItem[] = [];
      try {
        // session 0 matches no chat, so this lists the whole library.
        const rows = await listLibrary(0, query.trim() || undefined, 8);
        papers = rows.map((r) => ({
          title: r.title,
          subtext: r.year ? String(r.year) : undefined,
          group: t("notes:mention.papers", "Papers in your library"),
          icon: <FileText className="size-[18px]" />,
          onItemClick: insert({ kind: "paper", paperId: r.paper_content_id, sectionKey: "", label: r.title }),
        }));
      } catch {
        /* the library is unreachable: sections still work */
      }
      return [...sections, ...papers];
    },
    [editor, noteId, paperContentId, t],
  );

  // The [[ picker: notes, papers, headings and sections (see linkPicker).
  const pickerItems = useCallback(
    async (query: string): Promise<DefaultReactSuggestionItem[]> => {
      const groups: Record<LinkItem["group"], string> = {
        notes: t("notes:link.picker.notes", "Notes"),
        papers: t("notes:link.picker.papers", "Papers"),
        headings: t("notes:link.picker.headings", "Headings"),
        sections: t("notes:link.picker.sections", "Paper sections"),
      };
      const untitled = t("notes:link.untitled", "Untitled");
      const rows = await linkItems(editor, noteId, query, untitled);
      if (!rows.length) {
        // A muted hint instead of an empty menu; picking it changes nothing.
        return [
          {
            title: t("notes:link.picker.none", "No matching notes or papers"),
            subtext: t("notes:link.picker.noneHint", "Type part of a title; # for its headings, ## for any note's"),
            size: "small",
            onItemClick: () => reopenLinkPicker(editor, query),
          },
        ];
      }
      // The muted line under a title: what the row is (a heading's or
      // section's: the note or paper it is in).
      const typeLine = (r: LinkItem): string | undefined => {
        switch (r.icon) {
          case "paperNote":
            return t("notes:link.kind.paperNote", "Paper note");
          case "chatNote":
            return t("notes:link.kind.chatNote", "Chat note");
          case "ideaNote":
            return t("notes:link.kind.ideaNote", "Idea note");
          case "paper":
            return [t("notes:link.kind.paper", "Paper"), r.subtext].filter(Boolean).join(" · ");
          default:
            return r.subtext;
        }
      };
      return rows.map((r) => {
        const Icon = LINK_ICONS[r.icon];
        return {
          title: r.title || untitled,
          subtext: typeLine(r),
          group: groups[r.group],
          icon: <Icon className="size-4" />,
          onItemClick: () =>
            r.complete !== undefined ? completeLinkQuery(editor, r) : insertNoteLink(editor, r.props),
        };
      });
    },
    [editor, noteId, t],
  );

  // The [[ menu grows out of the caret it was typed at and shrinks back
  // into it (no motion when reduced motion is asked for).
  const pickerMotion = useMemo<FloatingUIOptions>(() => {
    const still = prefersReducedMotion();
    return {
      // Placed by top/left: the transform is the scale's.
      useFloatingOptions: { transform: false },
      useTransitionStylesProps: {
        duration: still ? 0 : 130,
        initial: { opacity: 0, transform: still ? "none" : "scale(0.95)" },
        common: ({ side }) => ({ transformOrigin: side === "top" ? "bottom left" : "top left" }),
      },
    };
  }, []);

  const loadColors = useNotesStore((s) => s.loadColors);
  useEffect(() => {
    void loadColors();
  }, [loadColors]);

  // Enter / ArrowDown in the page title: the caret goes to the start of the
  // first block (a new line before it when that block takes no text).
  const enterFirstBlock = useCallback(() => {
    const first = editor.document[0];
    const takesText = first && Array.isArray(first.content);
    const line = takesText
      ? first
      : first
        ? editor.insertBlocks([{ type: "paragraph" }] as never, first, "before")[0]
        : undefined;
    if (!line) return;
    editor.setTextCursorPosition(line, "start");
    editor.focus();
  }, [editor]);

  // Find in the note: Ctrl+F (Cmd+F on a Mac) anywhere inside this editor.
  // `findOpen` false with the bar still mounted is its exit slide.
  const [findMounted, setFindMounted] = useState(false);
  const [findOpen, setFindOpen] = useState(false);
  const [findSeq, setFindSeq] = useState(0);
  const onFindKey = useCallback((e: KeyboardEvent) => {
    if (!isFindKey(e)) return;
    e.preventDefault();
    e.stopPropagation();
    setFindMounted(true);
    setFindOpen(true);
    setFindSeq((n) => n + 1);
  }, []);
  // The pane's request (its toolbar's Find): adjusted while rendering, as
  // deriving state from a prop should be.
  const [seenFindRequest, setSeenFindRequest] = useState(findRequest);
  if (findRequest !== seenFindRequest) {
    setSeenFindRequest(findRequest);
    setFindMounted(true);
    setFindOpen(true);
    setFindSeq((n) => n + 1);
  }
  const onFindGone = useCallback(() => setFindMounted(false), []);

  // Ctrl+Shift+[ / ] (⌘⇧[ / ] on a Mac): fold / unfold the caret's heading
  // (or list item): the block itself, else the fold the caret is in.
  const onFoldKey = useCallback(
    (e: KeyboardEvent) => {
      const mac = /Mac|iPhone|iPad/.test(navigator.platform);
      if (variant !== "pane" || !e.shiftKey || e.altKey || !(mac ? e.metaKey : e.ctrlKey)) return;
      if (e.code !== "BracketLeft" && e.code !== "BracketRight") return;
      e.preventDefault();
      e.stopPropagation();
      let blockId: string;
      try {
        blockId = editor.getTextCursorPosition().block.id;
      } catch {
        return;
      }
      const owner = caretFoldOwner(editor.document, blockId);
      if (!owner) {
        toast(t("notes:fold.noHeading", "Put the caret in a heading or under one first"));
        return;
      }
      const store = useNotesStore.getState();
      const cur = store.folded[noteId] ?? [];
      const fold = e.code === "BracketLeft";
      if (fold === cur.includes(owner)) return;
      // Folding from inside it: the caret goes to the end of the heading.
      if (fold && owner !== blockId) {
        const block = editor.getBlock(owner);
        if (block && Array.isArray(block.content)) editor.setTextCursorPosition(block, "end");
      }
      store.toggleFold(noteId, owner);
    },
    [editor, noteId, t, variant],
  );

  const defaultTitle = useNotesStore((s) => s.notes[noteId]?.defaultTitle);
  const cardMarkId = card?.markId ?? null;
  const onJump = card?.onJump;
  const ctx = useMemo(
    () => ({
      noteId,
      paperContentId,
      // The paper's own title (the note's page title may be renamed).
      paperTitle: paperContentId !== null ? defaultTitle || note.title : null,
      card: cardMarkId !== null && onJump ? { markId: cardMarkId, onJump } : null,
      // A reference block's hover: Turn into inline citation.
      turnIntoCite:
        paperContentId !== null
          ? (blockId: string, markId: string) =>
              void turnBlockIntoCite(editor, blockId, markId, paperContentId)
          : undefined,
    }),
    [noteId, paperContentId, note.title, defaultTitle, cardMarkId, onJump, editor],
  );

  return (
    <NoteEditorContext.Provider value={ctx}>
      <div
        data-testid="note-editor"
        data-variant={variant}
        onClick={comments.onEditorClick}
        onKeyDownCapture={(e) => {
          onFindKey(e);
          if (!e.defaultPrevented) onFoldKey(e);
        }}
        onClickCapture={(e) => {
          // A paper link made from selected words opens the paper, like its chip.
          const a = (e.target as HTMLElement).closest?.("a[href^='paperhub://']");
          const target = a ? parseMentionHref(a.getAttribute("href") ?? "") : null;
          if (!target) return;
          e.preventDefault();
          useCanvasStore.getState().openPaper(target.paperId);
        }}
        data-lang={i18n.language}
        data-guides={tableGuides ? "on" : "off"}
        className={cn(
          "paperhub-notes min-w-0 animate-in fade-in-0 duration-200 motion-reduce:animate-none",
          variant === "pane" ? "h-full overflow-y-auto py-2" : "py-1 text-[13px]",
        )}
      >
        {findMounted && (
          <NoteFindBar
            editor={editor}
            noteId={noteId}
            cardPart={cardPart}
            focusSeq={findSeq}
            open={findOpen}
            onClose={() => setFindOpen(false)}
            onGone={onFindGone}
            host={findHost}
            compact={variant === "card"}
          />
        )}
        <div
          aria-live="polite"
          data-state={retrying ? "open" : "closed"}
          className="grid grid-rows-[1fr] transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
        >
          <div className="min-h-0 overflow-hidden">
            <p className="mx-3 mb-1 flex items-center gap-1.5 rounded-md bg-amber-500/10 px-2 py-1 text-xs text-amber-700 dark:text-amber-300">
              <CloudOff className="size-3.5 shrink-0" aria-hidden />
              {retrying ? t("notes:retrying", "Not saved — retrying") : null}
            </p>
          </div>
        </div>
        {variant === "pane" && <NoteTitle noteId={noteId} onExitDown={enterFirstBlock} />}
        {variant === "pane" && onOpenGuide && <EditorTip onOpenGuide={onOpenGuide} />}
        <UnsavedDrafts noteId={noteId} />
        <BlockNoteView
          editor={editor}
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          slashMenu={false}
          sideMenu={false}
          formattingToolbar={false}
          tableHandles={false}
          onChange={onChange}
          onSelectionChange={onSelectionChange}
        >
          <NoteFormattingBar onComment={comments.start} hidden={findOpen} />
          <NoteSideMenu />
          {variant === "pane" && <FoldControls editor={editor as never} noteId={noteId} hosts={foldHosts} />}
          <NoteTableHandles />
          <TableCaptions hosts={captionHosts} />
          {/* No blocks inside a table cell (as BlockNote's own slash menu). */}
          <SuggestionMenuController
            triggerCharacter="/"
            getItems={slashItems}
            shouldOpen={(state) => !state.selection.$from.parent.type.isInGroup("tableContent")}
          />
          <SuggestionMenuController triggerCharacter="@" getItems={mentionItems} />
          {/* BlockNote opens a two-character trigger itself: the second [ after
              a [ opens it; a pick replaces "[[query", Esc leaves it as typed. */}
          <SuggestionMenuController
            triggerCharacter="[["
            getItems={pickerItems}
            shouldOpen={opensLinkPicker}
            suggestionMenuComponent={LinkPickerMenu}
            floatingUIOptions={pickerMotion}
          />
        </BlockNoteView>
        {comments.popover}
        {suggestionBars}
        <LostCommentBadges badges={lostBadges.badges} host={lostBadges.host} onOpen={comments.openThread} />
      </div>
    </NoteEditorContext.Provider>
  );
}
