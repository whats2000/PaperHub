import { useTranslation } from "react-i18next";

import { useCanvasStore } from "@/store/canvas";

interface Props {
  chunkId: number;
  ordinal: number;
}

/** Academic-style bracketed citation, `[1]` (FR-03; bracketed since
 * 2026-11-15: a bare superscript digit was easy to miss). Clicking opens the
 * Citation Canvas on the cited chunk. */
export function CitationMarker({ chunkId, ordinal }: Props) {
  const { t } = useTranslation("canvas");
  const openCitation = useCanvasStore((s) => s.openCitation);
  return (
    <sup>
      <button
        type="button"
        aria-label={t("marker.citationAria", { ordinal })}
        onClick={() => openCitation(chunkId)}
        className="mx-0.5 cursor-pointer rounded px-0.5 text-[0.75rem] leading-none font-medium text-primary hover:bg-primary/10 hover:underline"
      >
        [{ordinal}]
      </button>
    </sup>
  );
}
