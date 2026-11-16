import { useState } from "react";
import { useTranslation } from "react-i18next";

import { pageCropUrl } from "@/lib/api";
import type { MarkPiece } from "@/types/notes";

import { useNoteEditorContext } from "./markProps";

/** The page crop of a piece with a box; nothing when there is none. */
export function CropImage({ piece, alt }: { piece: MarkPiece | undefined; alt: string }) {
  const { t } = useTranslation();
  const { paperContentId } = useNoteEditorContext();
  const [failed, setFailed] = useState(false);
  if (paperContentId === null || !piece?.box || typeof piece.page !== "number" || failed) {
    return null;
  }
  return (
    <figure className="m-0 mt-1 overflow-hidden rounded-md border border-border bg-white">
      <img
        src={pageCropUrl(paperContentId, piece.page, piece.box)}
        alt={alt}
        loading="lazy"
        className="mx-auto block max-h-64 w-auto max-w-full"
        onError={() => setFailed(true)}
      />
      <figcaption className="px-2 pb-1 text-center font-sans text-[10px] text-neutral-500">
        {t("notes:ref.pageCrop", "Page {{page}} crop", { page: piece.page + 1 })}
      </figcaption>
    </figure>
  );
}
