import { ImageIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EVIDENCE_MARKER_CLASS } from "@/lib/figureCitations";
import { useCanvasStore } from "@/store/canvas";

interface Props {
  paper: number;
  figureId: string;
  ordinal: number;
}

/** Superscript citation of a figure the agent looked at (`[fig:P/ID]`).
 * Clicking it opens the reader on that figure and highlights it, the same
 * way a `[chunk:N]` citation opens on its passage. */
export function FigureCitationMarker({ paper, figureId, ordinal }: Props) {
  const { t } = useTranslation("canvas");
  const openFigure = useCanvasStore((s) => s.openFigure);
  return (
    <sup>
      <button
        type="button"
        aria-label={t("marker.figureAria", { ordinal })}
        className={EVIDENCE_MARKER_CLASS}
        onClick={() => openFigure(paper, figureId)}
      >
        <ImageIcon className="h-[1em] w-[1em]" aria-hidden />
        {ordinal}
      </button>
    </sup>
  );
}
