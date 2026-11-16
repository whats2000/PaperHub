/** The notes editor: BlockNote with PaperHub's blocks, synced block by block.
 *
 * Each change is diffed against the last synced document by stable block id
 * (`sync.diffDocuments`) and queued in the notes store, which sends one burst
 * of ops per pause in typing. Conflicted blocks come back through the store's
 * `reloads` and are replaced in place, so the caret elsewhere is kept.
 */
import { filterSuggestionItems, insertOrUpdateBlockForSlashMenu } from "@blocknote/core";
import { SuggestionMenu } from "@blocknote/core/extensions";
import { en, ja, zh, zhTW } from "@blocknote/core/locales";
import {
  getDefaultReactSlashMenuItems,
  SuggestionMenuController,
  useCreateBlockNote,
  type DefaultReactSuggestionItem,
} from "@blocknote/react";
import { BlockNoteView } from "@blocknote/shadcn";
import "@blocknote/shadcn/style.css";
import { syntaxHighlighter } from "@blocknote/code-block";
import { AtSign, CloudOff, FileText, Hash, Lightbulb, Sigma, TextQuote } from "lucide-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { listLibrary } from "@/lib/api";
import { prefersReducedMotion } from "@/lib/motion";
import { propText } from "@/lib/notes/ops";
import { cn } from "@/lib/utils";
import { useCanvasStore } from "@/store/canvas";
import { useNotesStore } from "@/store/notes";

import { NoteEditorContext, sectionLabel } from "./blocks/markProps";
import { EditorTip } from "./EditorTip";
import { insertMention, parseMentionHref, type MentionTarget } from "./formatActions";
import { NoteFormattingBar } from "./FormattingBar";
import { lostCommentBadges, useBadgeRefresh, useLostCommentBadges } from "./commentBadges";
import { LostCommentBadges } from "./LostCommentBadges";
import { commentKeys, useNoteComments, type ReanchorHint } from "./noteComments";
import { noteSuggestionMarks, useNoteSuggestions, useSuggestionHosts } from "./noteSuggestions";
import { codeFence, dividerRule, inlineMarks } from "./inlineMarks";
import { partOf } from "./parts";
import { noteParts, refreshPartDecorations, type PartsView } from "./partsDecorations";
import { noteSchema, paperhubKeys } from "./schema";
import { UnsavedDrafts } from "./UnsavedDrafts";
import {
  diffDocuments,
  pruneUnsavedEmpty,
  toEditorBlocks,
  treeIds,
  type EditorBlock,
} from "./sync";

export interface NoteEditorProps {
  noteId: number;
  /** "pane" fills the Notes pane; "card" is the compact note card. */
  variant: "pane" | "card";
  /** The block holding the caret, as it moves. */
  onBlockFocus?: (blockId: string) => void;
  /** Opens the shortcuts guide (the tip's "All shortcuts"). */
  onOpenGuide?: () => void;
}

const DICTIONARIES = { en, ja, "zh-TW": zhTW, "zh-CN": zh } as const;

/** What the part decorations draw for a note (Focus fades, folds). */
function partsViewOf(noteId: number): PartsView {
  const s = useNotesStore.getState();
  return { focusOn: s.focusOn, activePart: s.activePart[noteId] ?? null, folded: new Set(s.folded[noteId] ?? []) };
}

function dictionaryFor(lang: string) {
  return DICTIONARIES[lang as keyof typeof DICTIONARIES] ?? (lang.startsWith("zh") ? zh : en);
}

export function NoteEditor(props: NoteEditorProps) {
  // Each note gets its own loading line and editor state.
  return <NoteEditorFrame key={props.noteId} {...props} />;
}

function NoteEditorFrame(props: NoteEditorProps) {
  const { t, i18n } = useTranslation();
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
      {loaded && <LoadedNoteEditor key={`${props.noteId}:${i18n.language}`} {...props} />}
    </div>
  );
}

function LoadedNoteEditor({ noteId, variant, onBlockFocus, onOpenGuide }: NoteEditorProps) {
  const { t, i18n } = useTranslation();
  const { resolvedTheme } = useTheme();
  const note = useNotesStore((s) => s.notes[noteId]!.note);
  const applyLocal = useNotesStore((s) => s.applyLocal);
  const setQuoteTarget = useNotesStore((s) => s.setQuoteTarget);
  const reload = useNotesStore((s) => s.reloads[noteId]);
  const retrying = useNotesStore((s) => s.retrying[noteId] ?? false);
  const focusOn = useNotesStore((s) => s.focusOn);
  const activePart = useNotesStore((s) => s.activePart[noteId] ?? null);
  const folded = useNotesStore((s) => s.folded[noteId]);
  const setActivePart = useNotesStore((s) => s.setActivePart);
  // The bars of the agent's suggestions are rendered into these hosts.
  const suggestionHost = useSuggestionHosts();
  // Badges for open comment threads whose words are gone.
  const lostBadges = useLostCommentBadges(noteId);

  const editor = useCreateBlockNote(
    {
      schema: noteSchema,
      initialContent: (() => {
        const tree = toEditorBlocks(useNotesStore.getState().notes[noteId]!.blocks);
        return tree.length ? (tree as never) : undefined;
      })(),
      dictionary: dictionaryFor(i18n.language),
      extensions: [
        paperhubKeys,
        commentKeys,
        inlineMarks,
        codeFence,
        dividerRule,
        syntaxHighlighter,
        noteParts(() => partsViewOf(noteId)),
        noteSuggestionMarks(() => ({
          suggestions: useNotesStore.getState().notes[noteId]?.suggestions ?? [],
          host: suggestionHost,
        })),
        lostCommentBadges(lostBadges.badgeView),
      ],
    },
    [noteId],
  );

  // What the server has (or will have once the queue lands), as editor blocks.
  const synced = useRef<EditorBlock[] | null>(null);
  const applyingRemote = useRef(false);
  const doc = useCallback(() => editor.document as unknown as EditorBlock[], [editor]);
  if (synced.current === null) {
    const known = treeIds(toEditorBlocks(useNotesStore.getState().notes[noteId]!.blocks));
    synced.current = pruneUnsavedEmpty(doc(), known);
  }

  // A fold outlives its divider only as a stale id: forget it.
  const keepFolds = useCallback(() => {
    const dividers = new Set<string>();
    const walk = (list: EditorBlock[]) =>
      list.forEach((b) => {
        if (b.type === "divider") dividers.add(b.id);
        walk(b.children);
      });
    walk(doc());
    useNotesStore.getState().keepFolds(noteId, dividers);
  }, [doc, noteId]);

  const onChange = useCallback(() => {
    keepFolds();
    if (applyingRemote.current || synced.current === null) return;
    const next = pruneUnsavedEmpty(doc(), treeIds(synced.current));
    const ops = diffDocuments(synced.current, next);
    synced.current = next;
    if (ops.length) applyLocal(noteId, ops);
  }, [applyLocal, doc, noteId, keepFolds]);

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

  // The part decorations read the store; redraw them when it changes.
  useEffect(() => {
    try {
      refreshPartDecorations(editor._tiptapEditor.view);
    } catch {
      /* not mounted yet: the decorations draw on mount */
    }
  }, [editor, focusOn, activePart, folded]);

  const onSelectionChange = useCallback(() => {
    let blockId: string;
    try {
      blockId = editor.getTextCursorPosition().block.id;
    } catch {
      return; // no caret (e.g. the editor lost focus)
    }
    onBlockFocus?.(blockId);
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
      const basic = t("notes:slash.groupBasic", "Basic blocks");
      const paperhub = t("notes:slash.groupPaperHub", "PaperHub");
      const items: DefaultReactSuggestionItem[] = [
        ...getDefaultReactSlashMenuItems(editor),
        {
          title: t("notes:slash.callout", "Callout"),
          subtext: t("notes:slash.calloutHint", "A tinted box that stands out"),
          aliases: ["callout", "tip", "note"],
          group: basic,
          icon: <Lightbulb className="size-[18px]" />,
          onItemClick: () => insertOrUpdateBlockForSlashMenu(editor, { type: "callout" } as never),
        },
        {
          title: t("notes:slash.equation", "Equation"),
          subtext: t("notes:slash.equationHint", "LaTeX, rendered as you type · $$"),
          aliases: ["equation", "math", "latex", "$$"],
          group: basic,
          icon: <Sigma className="size-[18px]" />,
          onItemClick: () => insertOrUpdateBlockForSlashMenu(editor, { type: "math" } as never),
        },
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
      return Promise.resolve(filterSuggestionItems(items, query));
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

  const loadColors = useNotesStore((s) => s.loadColors);
  useEffect(() => {
    void loadColors();
  }, [loadColors]);

  const ctx = useMemo(
    () => ({ noteId, paperContentId, paperTitle: paperContentId !== null ? note.title : null }),
    [noteId, paperContentId, note.title],
  );

  return (
    <NoteEditorContext.Provider value={ctx}>
      <div
        data-testid="note-editor"
        onClick={comments.onEditorClick}
        onClickCapture={(e) => {
          // A paper link made from selected words opens the paper, like its chip.
          const a = (e.target as HTMLElement).closest?.("a[href^='paperhub://']");
          const target = a ? parseMentionHref(a.getAttribute("href") ?? "") : null;
          if (!target) return;
          e.preventDefault();
          useCanvasStore.getState().openPaper(target.paperId);
        }}
        data-lang={i18n.language}
        className={cn(
          "paperhub-notes min-w-0 animate-in fade-in-0 duration-200 motion-reduce:animate-none",
          variant === "pane" ? "h-full overflow-y-auto py-2" : "max-h-80 overflow-y-auto py-1 text-[13px]",
        )}
      >
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
        {variant === "pane" && onOpenGuide && <EditorTip onOpenGuide={onOpenGuide} />}
        <UnsavedDrafts noteId={noteId} />
        <BlockNoteView
          editor={editor}
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          slashMenu={false}
          formattingToolbar={false}
          onChange={onChange}
          onSelectionChange={onSelectionChange}
        >
          <NoteFormattingBar onComment={comments.start} />
          <SuggestionMenuController triggerCharacter="/" getItems={slashItems} />
          <SuggestionMenuController triggerCharacter="@" getItems={mentionItems} />
        </BlockNoteView>
        {comments.popover}
        {suggestionBars}
        <LostCommentBadges badges={lostBadges.badges} host={lostBadges.host} onOpen={comments.openThread} />
      </div>
    </NoteEditorContext.Provider>
  );
}
