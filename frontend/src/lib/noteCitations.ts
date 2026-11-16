/** A `[note:B]` chip in the chat: open the notes pane at that block, in
 * whichever note holds it (a paper's note opens with its paper in the reader). */
import { toast } from "sonner";

import { getNoteBlockPlace } from "@/lib/api";
import i18n from "@/lib/i18n";
import { useNotesPaneStore } from "@/store/notesPane";

export async function openNoteBlock(blockId: string): Promise<void> {
  try {
    const place = await getNoteBlockPlace(blockId);
    const id = Number(place.target_id);
    if (Number.isNaN(id)) {
      useNotesPaneStore.setState({ open: true, activeTab: "notes" });
      return;
    }
    useNotesPaneStore.getState().openAt({ kind: place.target_kind, id, noteId: place.note_id, blockId });
  } catch {
    toast(i18n.t("notes:cite.gone", "That line is no longer in your notes."));
  }
}
