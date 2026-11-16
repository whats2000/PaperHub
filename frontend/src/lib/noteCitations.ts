/** Open the notes pane at a note block. The notes pane arrives in H5b.1 phase
 * 3; until it exists a `[note:B]` chip has nowhere to go, so this is a no-op
 * hook the pane's store replaces. */
export function openNoteBlock(blockId: string): void {
  void blockId;
}
