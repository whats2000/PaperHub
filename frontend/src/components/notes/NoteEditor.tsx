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
import { AtSign, FileText, Hash, Lightbulb, Sigma, TextQuote } from "lucide-react";
import { useTheme } from "next-themes";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { cn } from "@/lib/utils";
import { listLibrary } from "@/lib/api";
import { propText } from "@/lib/notes/ops";
import { useNotesStore } from "@/store/notes";

import { NoteEditorContext, sectionLabel } from "./blocks/markProps";
import { noteSchema, paperhubKeys } from "./schema";
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
}

const DICTIONARIES = { en, ja, "zh-TW": zhTW, "zh-CN": zh } as const;

function dictionaryFor(lang: string) {
  return DICTIONARIES[lang as keyof typeof DICTIONARIES] ?? (lang.startsWith("zh") ? zh : en);
}

export function NoteEditor(props: NoteEditorProps) {
  const { t } = useTranslation();
  const loaded = useNotesStore((s) => s.notes[props.noteId] !== undefined);
  if (!loaded) {
    return (
      <div className="px-4 py-3 text-sm text-muted-foreground" role="status">
        {t("notes:loading", "Loading the note…")}
      </div>
    );
  }
  // A new note is a new editor (its history and schema state start fresh).
  return <LoadedNoteEditor key={props.noteId} {...props} />;
}

function LoadedNoteEditor({ noteId, variant, onBlockFocus }: NoteEditorProps) {
  const { t, i18n } = useTranslation();
  const { resolvedTheme } = useTheme();
  const note = useNotesStore((s) => s.notes[noteId]!.note);
  const applyLocal = useNotesStore((s) => s.applyLocal);
  const setQuoteTarget = useNotesStore((s) => s.setQuoteTarget);
  const reload = useNotesStore((s) => s.reloads[noteId]);

  const editor = useCreateBlockNote(
    {
      schema: noteSchema,
      initialContent: (() => {
        const tree = toEditorBlocks(useNotesStore.getState().notes[noteId]!.blocks);
        return tree.length ? (tree as never) : undefined;
      })(),
      dictionary: dictionaryFor(i18n.language),
      extensions: [paperhubKeys, syntaxHighlighter],
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

  const onChange = useCallback(() => {
    if (applyingRemote.current || synced.current === null) return;
    const next = pruneUnsavedEmpty(doc(), treeIds(synced.current));
    const ops = diffDocuments(synced.current, next);
    synced.current = next;
    if (ops.length) applyLocal(noteId, ops);
  }, [applyLocal, doc, noteId]);

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
  }, [reload, editor, noteId, doc]);

  const onSelectionChange = useCallback(() => {
    if (!onBlockFocus) return;
    try {
      onBlockFocus(editor.getTextCursorPosition().block.id);
    } catch {
      /* no caret (e.g. the editor lost focus) */
    }
  }, [editor, onBlockFocus]);

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
      const insert = (props: { kind: "paper" | "section"; paperId: number; sectionKey: string; label: string }) => () =>
        editor.insertInlineContent([{ type: "mention", props } as never, " "]);
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
        className={cn(
          "paperhub-notes min-w-0",
          variant === "pane" ? "h-full overflow-y-auto py-2" : "max-h-80 overflow-y-auto py-1 text-[13px]",
        )}
      >
        <BlockNoteView
          editor={editor}
          theme={resolvedTheme === "dark" ? "dark" : "light"}
          slashMenu={false}
          onChange={onChange}
          onSelectionChange={onSelectionChange}
        >
          <SuggestionMenuController triggerCharacter="/" getItems={slashItems} />
          <SuggestionMenuController triggerCharacter="@" getItems={mentionItems} />
        </BlockNoteView>
      </div>
    </NoteEditorContext.Provider>
  );
}
