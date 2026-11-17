/** Export of the note shown: which formats work here (Word needs pandoc on
 * the server) and the download itself, saved under the server's file name. */
import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { exportNote, getNoteExportCapabilities, type NoteExportFormat } from "@/lib/api";

function save(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function useNoteExport(noteId: number | null) {
  const { t } = useTranslation();
  // Unknown until the server answers: Word stays offered meanwhile (a 409
  // still explains itself).
  const [docxAvailable, setDocxAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    getNoteExportCapabilities().then(
      (caps) => {
        if (!cancelled) setDocxAvailable(caps.docx);
      },
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, []);

  const onExport = useCallback(
    async (format: NoteExportFormat) => {
      if (noteId === null) return;
      try {
        const { blob, filename } = await exportNote(noteId, format);
        save(blob, filename);
      } catch (err) {
        const pandoc = err instanceof Error && err.message.includes("pandoc_missing");
        if (pandoc) setDocxAvailable(false);
        toast.error(
          pandoc
            ? t("notes:toolbar.exportNoPandoc", "Word export needs pandoc: install it with winget install pandoc")
            : t("notes:toolbar.exportFailed", "Couldn't export the note"),
        );
      }
    },
    [noteId, t],
  );

  return { onExport, docxAvailable: docxAvailable !== false };
}
