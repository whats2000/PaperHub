# Plan H5b design prototype

The interactive design for Plan H5b, the notes layer (#51). It is the **visual base for the H5b.1 implementation plan**: match its layout, states, copy and motion. The settled decisions behind it are in SRS §III-10.5 (H5b).

- **View it:** <https://claude.ai/artifact/TALnyw2Wgriiq69nmBmLxS> (private artifact; ask the owner for access).
- **Built on the shipped chat page**, captured from the running app first: the 260px sidebar, the header-less chat, the composer's icon row that opens the right panel, and the reader's rows (paper tabs + close, then HTML | PDF). Example content is two real library papers: *Toward a Predictive eXtended Reality Teleoperation System with Duo-Virtual Spaces* (Zhang, Liu, Kim, 2024; an HTML paper) and *A Review of Ghost Imaging via Sparsity Constraints* (Han et al., Appl. Sci. 2018; PDF only, drawn at its Marker block positions).
- **Revised after a simulated-user survey** (2026-11-16; a robotics PhD student, an on-prem industrial engineer, a lab PI and an equation-heavy theorist). What changed and why is in the SRS H5b "Simulated-user survey" bullet.

## What the prototype shows

- **Opening Notes:** a **Notes** button in the composer's icon row, beside the reader's. With Reader and Notes both on, the right panel shows **Reader | Notes tabs**; dragged wider than ~780 px it switches to **side by side** (Notes keeps its width; the left edge resizes only the Reader; the middle divider trades width). No stacked split. Opening a pane never resets the width you dragged.
- **One note per paper, organised by its section headings.** A highlight or quote lands under its section's heading (created when first needed). The notes pane has the paper's note and a secondary "This chat" tab; it scrolls to the section you're reading.
- **Marking text:** select text in the paper (HTML or a PDF page's text layer); the popover sits **below** the selection with four **named colours** (Key result, Method, Question, Disagree — renameable in My marks), Quote to note and Add note. A short confirmation says where it went ("Saved to XR teleoperation paper › §2 · Show").
- **Marking a figure or equation in one click:** hover Figure 3 (HTML) or Eq. (1) on the PDF's page 2 → "Mark figure" / "Mark equation". The figure becomes a figure block (image, caption, number, page); the equation a math block (editable LaTeX, the page crop beside it, "transcribed by the model", number and page).
- **Scanned pages:** drag a box (page 3); the note shows the OCR text next to the page crop so numbers can be checked.
- **Opening a mark's note:** with the notes pane visible, hovering a mark outlines its block there. Otherwise **click** a mark: the note card opens in the gutter left of the reader (placed like the PDF translation card), showing that section of the paper's note with the quoting block outlined, **editable in place**, with Open in Notes. With translation on (文A), the paragraph's translation card **stacks under** the note card.
- **The assistant's change card:** one line ("Added 3 notes under §2 in the paper's note · 1 suggestion on your text") with Open in notes and Undo; Details folds out the diff. Undo removes only its blocks and suggestion.
- **My marks:** the colours with their counts, rename in place, a filter per colour, and "Show my marks on the paper".
- **Quote source chips** jump to the passage (flash) with a "Back to note" pill; anchors not re-found after re-ingest show "Anchor lost" and keep the quote.
- **Notes page:** a rough H5b.2 sketch only; it will be redesigned from the shipped Ideas / Feedback pages before H5b.2 is planned.
- **Highlight palette:** validated with the dataviz validator in both themes (light `#2f9e63 #3b7dd8 #d6457e #8b5cd6`, dark `#36a067 #4b86dc #d9578a #9670dc`); every colour also has a name, so colour is never the only cue. Yellow stays reserved for citations.

## Files

| File | What it is |
| --- | --- |
| `h5b_prototype.html` | The prototype source, published as-is (the artifact host adds the document skeleton). |
