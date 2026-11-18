/** Citation markers in a note (`[chunk:12]`, `[note:b_1]`, `[fig:3/fig-1]`,
 * `[view:3/2/100,100,500,400]`, `[unsourced:not_read]`) drawn as chips.
 *
 * A chip is an inline decoration over the marker's own text, never a node:
 * the block's text stays the literal marker, so the server, export, search
 * and the outbound check read exactly what was typed or written. A chip
 * adds no width (no padding, margin or border; an outline for the dashed
 * unsourced one), so the caret moves through it as through any text. A
 * click opens the source the way the chat's citations do.
 */
import { createExtension } from "@blocknote/core";
import type { Node as PMNode } from "@tiptap/pm/model";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";

import { CHUNK_MARKER_RE, parseChunkIds } from "@/lib/chunkCitations";
import { FIG_MARKER_RE, NOTE_MARKER_RE, parseFigureKeys, parseNoteIds, parseViewKey, VIEW_MARKER_RE } from "@/lib/figureCitations";
import i18n from "@/lib/i18n";
import { openNoteBlock } from "@/lib/noteCitations";
import { UNSOURCED_REASONS, UNSOURCED_RE } from "@/lib/unsourcedMarks";
import { useCanvasStore } from "@/store/canvas";

export type CiteKind = "chunk" | "note" | "fig" | "view" | "unsourced";

export interface Cite {
  kind: CiteKind;
  /** The marker's capture (id list, key list, view key or reason). */
  value: string;
}

const KINDS: [CiteKind, RegExp][] = [
  ["chunk", CHUNK_MARKER_RE],
  ["note", NOTE_MARKER_RE],
  ["fig", FIG_MARKER_RE],
  ["view", VIEW_MARKER_RE],
  ["unsourced", UNSOURCED_RE],
];

/** Every marker of the shapes `edit_note` validates, combined: one group
 * per kind, in `KINDS` order. */
export const CITE_RE = new RegExp(KINDS.map(([, re]) => `(?:${re.source.replace(/\((?!\?)/g, "(?:")})`).join("|"), "g");
const ONE = KINDS.map(([kind, re]) => [kind, new RegExp(`^${re.source}$`)] as const);

/** The citations in `text`: each with its range and what it cites. */
export function findCites(text: string): { from: number; to: number; cite: Cite }[] {
  const out: { from: number; to: number; cite: Cite }[] = [];
  for (const m of text.matchAll(CITE_RE)) {
    const whole = m[0];
    const hit = ONE.map(([kind, re]) => [kind, re.exec(whole)] as const).find(([, r]) => r);
    if (!hit?.[1]) continue;
    out.push({ from: m.index, to: m.index + whole.length, cite: { kind: hit[0], value: hit[1][1] ?? "" } });
  }
  return out;
}

/** What hovering a chip says. */
export function citeTitle(cite: Cite): string {
  switch (cite.kind) {
    case "chunk":
      return i18n.t("notes:cite.chunkTitle", "Open the cited passage");
    case "note":
      return i18n.t("notes:cite.noteTitle", "Open the cited line of your notes");
    case "fig":
      return i18n.t("notes:cite.figTitle", "Open the cited figure");
    case "view":
      return i18n.t("notes:cite.viewTitle", "Open the cited page region");
    case "unsourced": {
      const known = (UNSOURCED_REASONS as readonly string[]).includes(cite.value);
      const why = known
        ? i18n.t(`canvas:unsourced.reason.${cite.value}`)
        : i18n.t("canvas:unsourced.reason.unknown", "the source could not be checked");
      return i18n.t("notes:cite.unsourcedTitle", { reason: why, defaultValue: "No checked source: {{reason}}" });
    }
  }
}

/** Opens what a chip cites: the Citation Canvas on a passage, figure or
 * page region, or the notes pane at a block. False for a chip that opens
 * nothing (unsourced) or a marker that names nothing. */
export function openCite(cite: Cite): boolean {
  const canvas = useCanvasStore.getState();
  switch (cite.kind) {
    case "chunk": {
      const id = parseChunkIds(cite.value)[0];
      if (id === undefined) return false;
      canvas.openCitation(id);
      return true;
    }
    case "fig": {
      const ref = parseFigureKeys(cite.value)[0];
      if (!ref) return false;
      canvas.openFigure(ref.paper, ref.figureId);
      return true;
    }
    case "view": {
      const ref = parseViewKey(cite.value);
      canvas.openView(ref.paper, ref.page, ref.box);
      return true;
    }
    case "note": {
      const id = parseNoteIds(cite.value)[0];
      if (!id) return false;
      void openNoteBlock(id);
      return true;
    }
    case "unsourced":
      return false;
  }
}

const CHIP = "ph-cite rounded-sm bg-primary/10 text-primary cursor-pointer hover:bg-primary/20 hover:underline";
const UNSOURCED =
  "ph-cite rounded-sm text-muted-foreground outline-1 -outline-offset-1 outline-dashed outline-muted-foreground/60";

function citeDecorations(doc: PMNode): DecorationSet {
  const decos: Decoration[] = [];
  doc.descendants((node, pos) => {
    if (!node.isText) return true;
    for (const { from, to, cite } of findCites(node.text ?? "")) {
      decos.push(
        Decoration.inline(
          pos + from,
          pos + to,
          {
            class: cite.kind === "unsourced" ? UNSOURCED : CHIP,
            "data-cite": cite.kind,
            "data-value": cite.value,
            title: citeTitle(cite),
          },
          { cite },
        ),
      );
    }
    return false;
  });
  return DecorationSet.create(doc, decos);
}

const key = new PluginKey<DecorationSet>("paperhubCitations");

export function citationChips() {
  return createExtension({
    key: "paperhubCitations",
    prosemirrorPlugins: [
      new Plugin<DecorationSet>({
        key,
        state: {
          init: (_config, state) => citeDecorations(state.doc),
          apply: (tr, set) => (tr.docChanged ? citeDecorations(tr.doc) : set),
        },
        props: {
          decorations: (state) => key.getState(state),
          handleDOMEvents: {
            click(_view, event) {
              if (event.button !== 0 || event.shiftKey) return false;
              const el = event.target instanceof Element ? event.target.closest<HTMLElement>(".ph-cite") : null;
              const kind = el?.dataset.cite as CiteKind | undefined;
              // The caret still lands where clicked: the marker stays editable.
              if (el && kind) openCite({ kind, value: el.dataset.value ?? "" });
              return false;
            },
          },
        },
      }),
    ],
  });
}
