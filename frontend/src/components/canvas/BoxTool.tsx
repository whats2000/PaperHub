/** The Box tool on PDF pages (Plan H5b.1): drag a rectangle to quote a
 * figure, a table, an equation or any region. On from the reader bar (PDF
 * only; Escape or the button stops it), and always on a page with no text
 * layer (a scan). What a box marks is `boxMark` (lib/marks/pdfGeometry);
 * the drag is `useBoxDrag`. */
import { SquareDashedMousePointer } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { usePresence } from "@/hooks/usePresence";
import { hasOpenLayer } from "@/lib/openLayer";
import type { Frac } from "@/lib/marks/pdfGeometry";
import { cn } from "@/lib/utils";
import { useMarksStore } from "@/store/marks";

/** "Box" in the reader bar (PDF only): a pressed toggle; Escape stops it,
 * and it stops when the reader leaves the PDF. */
export function BoxToolButton({ shown }: { shown: boolean }) {
  const { t } = useTranslation("notes");
  const on = useMarksStore((s) => s.boxOn);
  const setOn = useMarksStore((s) => s.setBoxOn);

  useEffect(() => {
    if (!shown && on) setOn(false);
  }, [shown, on, setOn]);
  useEffect(() => {
    if (!on) return;
    // On the window, after the document's own listeners: an Escape a
    // popover, menu or dialog took (or one closing an open layer) is theirs.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || hasOpenLayer()) return;
      setOn(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [on, setOn]);

  return (
    <button
      type="button"
      aria-pressed={on}
      aria-hidden={!shown || undefined}
      inert={!shown ? true : undefined}
      title={t(
        "marks.boxHint",
        "Box: drag a rectangle on the page to quote a figure, an equation or any region. Esc to stop.",
      )}
      onClick={() => {
        if (!on) window.getSelection()?.removeAllRanges();
        setOn(!on);
      }}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded px-2 py-0.5 text-xs font-medium whitespace-nowrap transition-[color,background-color,opacity,box-shadow] duration-150 motion-reduce:transition-none",
        on
          ? "bg-foreground/10 text-foreground shadow-[inset_0_-2px_0_var(--foreground)]"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
        !shown && "pointer-events-none opacity-0",
      )}
    >
      <SquareDashedMousePointer aria-hidden className="size-3.5" />
      {t("marks.box", "Box")}
    </button>
  );
}

const pct = (v: number) => `${Math.round(v * 100000) / 1000}%`;

/** A dashed box on the page: the one being drawn, or the one waiting for
 * its popover choice. It fades out when it goes. */
export function DraftBox({ box }: { box: Frac | null }) {
  const presence = usePresence(box !== null, 150);
  const [shown, setShown] = useState(box);
  if (box && box !== shown) setShown(box);
  if (!presence.mounted || !shown) return null;
  return (
    <div
      data-draft-box
      data-state={presence.state}
      aria-hidden
      style={{ left: pct(shown[0]), top: pct(shown[1]), width: pct(shown[2] - shown[0]), height: pct(shown[3] - shown[1]) }}
      className={cn(
        "pointer-events-none absolute border-[1.5px] border-dashed border-[var(--hl-blue)] bg-[color-mix(in_srgb,var(--hl-blue)_12%,transparent)]",
        "transition-opacity duration-150 ease-out motion-reduce:transition-none data-[state=closed]:opacity-0",
      )}
    />
  );
}
