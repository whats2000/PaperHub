/** Export of the note shown: which formats work here (Word needs pandoc 2.15+
 * on the server) and the download itself, saved under the server's file name.
 * Word draws the tables in the viewer's table style (three-line or grid). */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  exportNote,
  getNoteExportCapabilities,
  type DocxUnavailableReason,
  type NoteExportFormat,
} from "@/lib/api";

function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked after the click has handed the download over, not during it.
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

const REASONS: DocxUnavailableReason[] = ["pandoc_missing", "pandoc_too_old"];

function reasonOf(err: unknown): DocxUnavailableReason | null {
  if (!(err instanceof Error)) return null;
  return REASONS.find((r) => err.message.includes(r)) ?? null;
}

export function useNoteExport(noteId: number | null) {
  const { t } = useTranslation();
  // Unknown until the server answers: Word stays offered meanwhile (a 409
  // still explains itself).
  const [docxReason, setDocxReason] = useState<DocxUnavailableReason | null>(null);

  useEffect(() => {
    let cancelled = false;
    getNoteExportCapabilities().then(
      (caps) => {
        if (!cancelled) setDocxReason(caps.docx ? null : (caps.docx_reason ?? "pandoc_missing"));
      },
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, []);

  /** Resolves once the download is saved (or has failed with a toast), so
   * the menu item stays busy until then. */
  const onExport = useCallback(
    async (format: NoteExportFormat) => {
      if (noteId === null) return;
      try {
        // Word's table captions read "Table 1." in the viewer's language.
        const label = t("notes:table.captionWord", "Table");
        const { blob, filename } = await exportNote(noteId, format, label);
        save(blob, filename);
      } catch (err) {
        const reason = reasonOf(err);
        if (reason) setDocxReason(reason);
        toast.error(
          reason === "pandoc_missing"
            ? t("notes:toolbar.exportNoPandoc", "Word export needs pandoc: install it with winget install pandoc")
            : reason === "pandoc_too_old"
              ? t(
                  "notes:toolbar.exportPandocTooOld",
                  "Word export needs pandoc 2.15 or newer: update it with winget upgrade pandoc",
                )
              : t("notes:toolbar.exportFailed", "Couldn't export the note"),
        );
      }
    },
    [noteId, t],
  );

  return { onExport, docxReason };
}
