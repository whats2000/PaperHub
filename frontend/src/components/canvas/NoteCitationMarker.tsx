import { NotebookText } from "lucide-react";
import { useTranslation } from "react-i18next";

import { EVIDENCE_MARKER_CLASS } from "@/lib/figureCitations";
import { openNoteBlock } from "@/lib/noteCitations";

interface Props {
  blockId: string;
  ordinal: number;
}

/** Bracketed citation (`[✎ 2]`) of a note block the agent read
 * (`[note:b_…]`). Clicking it opens the notes pane at that block. */
export function NoteCitationMarker({ blockId, ordinal }: Props) {
  const { t } = useTranslation("canvas");
  return (
    <sup>
      <button
        type="button"
        aria-label={t("marker.noteAria", { ordinal })}
        className={EVIDENCE_MARKER_CLASS}
        onClick={() => openNoteBlock(blockId)}
      >
        [<NotebookText className="h-[1em] w-[1em]" aria-hidden />
        {ordinal}]
      </button>
    </sup>
  );
}
