/** A `[note:B]` chip in the chat: open the notes pane at that block, in
 * whichever note holds it (a paper's note opens with its paper in the reader). */
import { toast } from "sonner";

import { ApiError, getNoteBlockPlace } from "@/lib/api";
import i18n from "@/lib/i18n";
import { useNotesPaneStore } from "@/store/notesPane";

export async function openNoteBlock(blockId: string): Promise<void> {
  try {
    const place = await getNoteBlockPlace(blockId);
    useNotesPaneStore
      .getState()
      .openAt({ kind: place.target_kind, id: Number(place.target_id), noteId: place.note_id, blockId });
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      toast(i18n.t("notes:cite.gone", "That line is no longer in your notes."));
    } else {
      toast.error(i18n.t("notes:cite.failed", "Couldn't open that note. Try again."));
    }
  }
}
