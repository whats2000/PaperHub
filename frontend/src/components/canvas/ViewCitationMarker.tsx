import { ScanSearch } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EVIDENCE_MARKER_CLASS } from "@/lib/figureCitations";
import { useCanvasStore, type ViewRequest } from "@/store/canvas";

interface Props {
  paper: number;
  /** 1-based page on the paper's page PDF. */
  page: number;
  box: ViewRequest["box"];
  ordinal: number;
}

/** Superscript citation of a page region the agent viewed
 * (`[view:P/N/box]`). Clicking it opens the reader on that page with the
 * region highlighted. */
export function ViewCitationMarker({ paper, page, box, ordinal }: Props) {
  const { t } = useTranslation("canvas");
  const openView = useCanvasStore((s) => s.openView);
  return (
    <sup>
      <button
        type="button"
        aria-label={t("marker.viewAria", { page, ordinal })}
        className={EVIDENCE_MARKER_CLASS}
        onClick={() => openView(paper, page, box)}
      >
        <ScanSearch className="h-[1em] w-[1em]" aria-hidden />
        {ordinal}
      </button>
    </sup>
  );
}
