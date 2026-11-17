/** The Marker blocks of a PDF's pages (Plan H5b.1), loaded per page as
 * the page comes into view, kept for the reader's life. A page without
 * blocks (the LaTeX-compiled PDF of an HTML paper) loads as an empty list;
 * a failed request rejects (never passes for "no blocks") and is tried
 * again on the next call. */
import { useCallback, useEffect, useRef, useState } from "react";

import { listPaperBlocks } from "@/lib/api";
import type { PaperPageBlock } from "@/types/notes";

const NO_BLOCKS: PaperPageBlock[] = [];

export function usePdfBlocks(paperId: number | null) {
  const [blocks, setBlocks] = useState<Record<number, PaperPageBlock[]>>({});
  /** The same, readable from callbacks without a re-render. */
  const blocksRef = useRef(blocks);
  useEffect(() => {
    blocksRef.current = blocks;
  }, [blocks]);
  const requests = useRef(new Map<number, Promise<PaperPageBlock[]>>());
  const loadBlocks = useCallback(
    (page: number): Promise<PaperPageBlock[]> => {
      if (paperId === null) return Promise.resolve(NO_BLOCKS);
      const have = blocksRef.current[page];
      if (have) return Promise.resolve(have);
      let req = requests.current.get(page);
      if (!req) {
        req = listPaperBlocks(paperId, page).then(
          (list) => {
            blocksRef.current = { ...blocksRef.current, [page]: list };
            setBlocks((prev) => ({ ...prev, [page]: list }));
            return list;
          },
          (err: unknown) => {
            requests.current.delete(page); // try again next time
            throw err;
          },
        );
        requests.current.set(page, req);
      }
      return req;
    },
    [paperId],
  );
  return { blocks, blocksRef, loadBlocks };
}
