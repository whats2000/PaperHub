# Plan H5b design prototype

The interactive design for Plan H5b, the notes layer (#51). It is the **visual base for the H5b.1–H5b.3 implementation plans**: match its layout, states, copy and motion. The settled decisions behind it are in SRS §III-10.5 (H5b).

- **View it:** <https://claude.ai/artifact/TALnyw2Wgriiq69nmBmLxS> (private artifact; ask the owner for access).
- **Chat with paper + notes (H5b.1, full fidelity):**
  - **Split right panel:** Paper / Paper + Notes / Notes, a draggable divider, stack or side by side, swap and close. The choice is remembered; narrow screens show one pane at a time.
  - **Reader marks:** a highlight is a tint in one of four colours (green, blue, pink, purple — yellow stays reserved for citations); a passage quoted to a note has a thin neutral underline; every marked passage has a margin icon (with a count when several notes point to it); a section with a note has a "note" marker by its heading. "My marks" in the reader header hides them all.
  - **Selecting text** opens a popover: four highlight colours, "Quote to note", "Add note". A highlight is a quote block in the section's note.
  - **Hovering a mark** never covers the text. With the notes pane open, it outlines the linked block in the pane. In Paper-only view, the margin icon (hover after a short delay, or a click / tap on the mark) opens a read-only card beside it with the note, scrolled to and outlining the quoting block; "Edit in Notes" opens the notes pane at that block.
  - **Quote blocks** carry a source chip (paper · § · page). Clicking it opens the reader at the passage and flashes the mark; a "Back to note" pill returns in one click and fades once you scroll away.
  - **Notes pane:** tabs for the section note (follows the reader's section unless pinned), this chat's note and the paper note. Blocks have a drag handle; AI-written blocks carry a small "AI" tag; an assistant change to your block is a tracked suggestion with Accept / Reject; an anchor that wasn't re-found after re-ingest shows "Anchor lost" and keeps the quote.
  - **The assistant's change card** in the chat: what it added, what it suggested, "Open in notes" and Undo. Undo removes its blocks and suggestion and leaves yours alone.
  - **Note citations** in answers are bracketed like other citations, with a note icon: `[📓 1]`.
- **Notes page (H5b.2, sketch):** a sidebar "Notes" entry; a page tree grouped Recent / Papers → sections / Chats / Ideas / Pages / Trash with search; the full-width editor with History and Export, a `/` menu (quote from a paper, figure, math, image, page link), a math block and backlinks.
- **Image block (H5b.3, sketch):** the original file with an Image / Text toggle; the text is a model-written transcription (marked, editable).
- **PDF marks (sketch):** the page stays untouched; a highlight is drawn over the page region and a quote as an underline.
- **Highlight palette:** validated with the dataviz validator — light `#2f9e63 #3b7dd8 #d6457e #8b5cd6` on the light surface, dark `#36a067 #4b86dc #d9578a #9670dc` on the dark surface; all checks pass in both themes. Each colour also has a name in the popover and the marks menu, so colour is never the only cue.
- **The prototype, not the production code.** One hand-built HTML page using the app's real tokens from `frontend/src/index.css`, Geist and lucide icons. Production UI is built from the real React components (the Citation Canvas becomes the Reader pane; the editor is BlockNote) with `t()` strings in all four locales.

## Files

| File | What it is |
| --- | --- |
| `h5b_prototype.html` | The prototype source, published as-is (the artifact host adds the document skeleton). |
