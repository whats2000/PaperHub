/** The highlighter on PDF pages (Plan H5b.1): a stroke on the text layer
 * marks at once in the pen colour, joining the pen session's mark in the
 * same paragraph — the Marker block, or on a page without blocks (the
 * LaTeX-compiled PDF of an HTML paper) the reading-text paragraph its text
 * located into (a piece not located never joins); a click on a figure or an
 * equation marks it whole; a box on a scanned page marks at once; a click on
 * a mark erases the piece under it. `usePdfMarks` wires it to the reader. */
import { useCallback, type RefObject } from "react";

import type { MarkableObject } from "@/components/canvas/ObjectMarkButton";
import { usePenSave, type PenPiece } from "@/components/canvas/useMarkSave";
import type { LocateResult } from "@/lib/api";
import { boxMark, captionFor, FIGURE_TYPES, type Frac, type LayerSpan, type PdfFragment, type PdfPiece } from "@/lib/marks/pdfGeometry";
import { clearIfStill, locatedPlace, pdfPlace } from "@/lib/marks/pieces";
import { useMarksStore } from "@/store/marks";
import type { MarkPiece, PaperPageBlock } from "@/types/notes";

const NO_BLOCKS: PaperPageBlock[] = [];

/** A piece placed in the paper's reading text (a page with no Marker
 * blocks): its chunks, its span and its paragraph there. */
export type Located = NonNullable<LocateResult["match"]>;

/** A text piece as stored (the section rides on the mark). */
export function storedPiece(p: PdfPiece, at?: Located): MarkPiece {
  // `raw` (the lines as laid out) lets the server decide each line-end
  // hyphen from the paper's words; it makes the stored quote from it.
  let piece: MarkPiece = { quote: p.quote, raw: p.raw, page: p.page, rects: p.rects };
  if (p.block_id) piece.block_id = p.block_id;
  if (at) {
    piece = {
      ...piece,
      chunk_start: at.chunk_start,
      chunk_end: at.chunk_end,
      text_start: at.text_start,
      text_end: at.text_end,
    };
  }
  return piece;
}

/** A figure (its caption) or an equation (its LaTeX) taken whole. */
export function objectPiece(
  kind: MarkableObject,
  page: number,
  b: PaperPageBlock,
  pageBlocks: readonly PaperPageBlock[],
): MarkPiece {
  return kind === "figure"
    ? { quote: captionFor(pageBlocks, b), page, block_id: b.block_id, box: b.box }
    : { quote: b.text, latex: b.text, page, block_id: b.block_id, box: b.box };
}

/** A text selection's pieces, each placed (`missed`: one wasn't found in
 * the reading text); null when they can't be had (already said why). */
export type PlaceText = (
  fragments: readonly PdfFragment[],
) => Promise<{ placed: { piece: PdfPiece; at?: Located }[]; missed: boolean } | null>;

export interface PdfPen {
  /** A stroke on the text layer (the selection it made, read when made). */
  stroke: (range: Range, fragments: PdfFragment[]) => void;
  /** A click on a figure or equation block. */
  object: (page: number, block: PaperPageBlock) => void;
  /** A box drawn on a page (a scanned one: the Box tool is off). */
  box: (page: number, frac: Frac, spans: LayerSpan[], scanned: boolean) => void;
  /** A click on a mark: erases the piece under it. */
  erase: (markId: string, piece: number) => void;
}

export function usePdfPen({
  paperId,
  placeText,
  loadPages,
  blocksRef,
  notLocated,
}: {
  paperId: number | null;
  placeText: PlaceText;
  loadPages: (pages: number[]) => Promise<PaperPageBlock[][] | null>;
  blocksRef: RefObject<Readonly<Record<number, PaperPageBlock[]>>>;
  /** Says a saved passage wasn't found in the paper's text. */
  notLocated: () => void;
}): PdfPen {
  const penSave = usePenSave(paperId);

  const stroke = useCallback(
    (range: Range, fragments: PdfFragment[]) => {
      let missed = false;
      penSave({
        view: "pdf",
        pieces: async () => {
          const r = await placeText(fragments);
          if (!r) return null;
          missed = r.missed;
          return r.placed.map(
            ({ piece, at }): PenPiece => ({
              section: at ? at.section : piece.section,
              piece: storedPiece(piece, at),
              // Its Marker block; without blocks, the paragraph it located
              // into (not located: never joins).
              place: piece.block_id ? pdfPlace(piece.page, piece.block_id) : locatedPlace(at?.paragraph),
            }),
          );
        },
        done: (ok) => {
          if (!ok) return;
          clearIfStill(document, range);
          if (missed) notLocated();
        },
      });
    },
    [penSave, placeText, notLocated],
  );

  const object = useCallback(
    (page: number, b: PaperPageBlock) => {
      const kind: MarkableObject = FIGURE_TYPES.has(b.type) ? "figure" : "equation";
      penSave({
        view: "pdf",
        kind,
        pieces: () => [
          { section: b.section, piece: objectPiece(kind, page, b, blocksRef.current?.[page] ?? NO_BLOCKS), place: null },
        ],
      });
    },
    [penSave, blocksRef],
  );

  const box = useCallback(
    (page: number, frac: Frac, spans: LayerSpan[], scanned: boolean) => {
      penSave({
        view: "pdf",
        kind: "region",
        pieces: async () => {
          const loaded = await loadPages([page]);
          if (!loaded) return null;
          // A box over most of an equation is that equation.
          const result = boxMark({ page, box: frac, blocks: loaded[0]!, spans, scanned });
          return [{ section: result.section, piece: result.piece, place: null, kind: result.kind }];
        },
      });
    },
    [penSave, loadPages],
  );

  const erase = useCallback((markId: string, piece: number) => {
    void useMarksStore.getState().erase(markId, piece);
  }, []);

  return { stroke, object, box, erase };
}
