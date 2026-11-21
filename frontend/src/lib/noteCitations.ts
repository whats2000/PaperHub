/** A `[note:B]` chip in the chat: open the notes pane at that block, in
 * whichever note holds it (a paper's note opens with its paper in the
 * reader, another chat's note in that chat, an idea's on the Ideas page). */
import { toast } from "sonner";

import { ApiError, getNoteBlockPlace } from "@/lib/api";
import i18n from "@/lib/i18n";
import { openNotePlace } from "@/lib/notes/openNoteLink";

export async function openNoteBlock(blockId: string): Promise<void> {
  try {
    const place = await getNoteBlockPlace(blockId);
    const opened = await openNotePlace({
      kind: place.target_kind,
      id: Number(place.target_id),
      noteId: place.note_id,
      blockId,
    });
    // Its chat or idea is not there: say so (it is not a failure to retry).
    if (!opened) toast(i18n.t("notes:link.unreachable", "Couldn't open that link: where it points is not there."));
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) {
      toast(i18n.t("notes:cite.gone", "That line is no longer in your notes."));
    } else {
      toast.error(i18n.t("notes:cite.failed", "Couldn't open that note. Try again."));
    }
  }
}
