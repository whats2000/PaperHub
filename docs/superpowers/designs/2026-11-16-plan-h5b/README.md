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
- **Marks on a translation:** the translate pill floats at the reader view's corner (文A on/off, 繁中 ▾ for the language). The first paragraph has a purple mark made on its 繁中 translation: with translation off it shows as a 文 marker beside the paragraph; its card offers Turn on translation, and with 日本語 selected, Switch to 繁中. Mark the translation of any paragraph (or text in the PDF translation card); the note tags it "Translated · 繁中" with Show original.
- **Highlighter mode:** the **Highlighter** button in the reader bar (colours beside it, Esc to stop). Every drag marks at once; strokes of one colour in one paragraph join into one mark with the skipped words left out ("reduces latency … by 40%", with Split); a click marks Figure 3 or Eq. (1) whole; a click on a mark erases it (one piece at a time).
- **Delete with Undo:** the trash button on the note card and on each note block (hover). Deleting a mark's block removes it from the paper too; your own text is kept. The toast's **Undo** then becomes **Delete again**, and the assistant card's Undo becomes **Apply again**, so an accidental Undo is one click back.
- **Box on PDF pages:** the **Box** button (PDF papers only) turns a drag into a box on any page — quote a figure, a table, an equation or any region. The note keeps the page crop with the text under the box; a box around Eq. (1) snaps to it and becomes an equation block. Scanned pages (page 3) always take a box, and the note shows the OCR text next to the crop so numbers can be checked.
- **Opening a mark's note:** with the notes pane visible, hovering a mark outlines its block there. Otherwise **click** a mark: the note card opens in the gutter left of the reader (placed like the PDF translation card), showing that section of the paper's note with the quoting block outlined, **editable in place**, with Open in Notes. With translation on (文A), the paragraph's translation card **stacks under** the note card.
- **The assistant's change card:** one line ("Added 3 notes under §2 in the paper's note · 1 suggestion on your text") with Open in notes and Undo; Details folds out the diff. Undo removes only its blocks and suggestion, then becomes Apply again.
- **The note editor works like Notion:** type in any block; Enter / Backspace / ↑ ↓ as in Notion; `# ` `- ` `1. ` `[] ` `" ` `---` `$$` shortcuts; `/` for the block menu (incl. Quote from the paper, Link a paper or section); `@` for paper / section links; hover a block for `+` and `⋮⋮` (drag to reorder, click for Turn into / Duplicate / Delete); select text for the formatting bar; equations render as you type.
- **Notes page (H5b.2 sketch), nested pages:** New page / + on a page; drag a page onto another to nest it (edges: before / after; onto "Your pages": top level); ⋯ → Move to… / Delete with Undo; a page with pages inside becomes a folder page with an "In this page" index on top; drag a paper, chat or idea note onto a page to move it there (the groups list only unsorted notes; Show all lists every note with where it is filed; drop it on its group to unsort); ⋯ → Also link in… adds a link in a second page; deleting a page sends its filed notes back to Unsorted and asks whether to move its own pages up; pick a page's icon via "Add icon" or the tree icon (it never changes by itself); fold the "In this page" index.
- **Notes toolbar:** Following §2 (click to stop the note scrolling with the paper) · Focus · ⋯ (the note's details, Export as Markdown / Word, Shortcuts). Links in the Notes page tree can be dragged to another page.
- **Nesting and Enter on references:** Tab nests a list item (or any text block) under the one above, Shift+Tab steps out; markers change per level (• ◦ ▪, 1. a. i.). Click a reference, heading or divider and press Enter to add a line below it.
- **Inline marks, code, comments, shortcuts:** type `**bold**` `*it*` `__u__` `~~s~~` `` `c` `` `||hidden||` (or select text and use the bar: B I U S · quote code hide · Link Comment); ```` ```python ```` + Enter for a highlighted code block (Ctrl+Enter leaves); select text → Comment, then reply / Resolve / Delete with Undo; the tip above the note and the Shortcuts button show everything the editor knows.
- **Dividers split notes:** type `---` on the note's last line (or in one of your own lines) and it becomes a divider (Undo turns it back, then offers Make a divider again). Only dividers split a section into notes. A new mark goes into the note you're working in ("§2 · note 2 of 3"). Hovering a reference darkens the two dividers around its note; there is no band in the editor. The note card from the paper folds the section's other notes to one line each and highlights only the clicked reference; references look unlike your notes (paper serif, a bar in the mark's colour, a "From the paper" tag), and every other reference is a Show ↗ jumper to its passage. **Focus** fades the other notes and, on the paper, every mark not quoted in this note; a divider's fold button collapses the note below it. Toasts use the theme's popover colours.
- **My marks:** the colours with their counts, rename in place, a filter per colour, and "Show my marks on the paper".
- **Quote source chips** jump to the passage (flash) with a "Back to note" pill; anchors not re-found after re-ingest show "Anchor lost" and keep the quote.
- **Notes page:** a rough H5b.2 sketch only; it will be redesigned from the shipped Ideas / Feedback pages before H5b.2 is planned.
- **Highlight palette:** validated with the dataviz validator in both themes (light `#2f9e63 #3b7dd8 #d6457e #8b5cd6`, dark `#36a067 #4b86dc #d9578a #9670dc`); every colour also has a name, so colour is never the only cue. Yellow stays reserved for citations.

## Where the build differs from the prototype (H5b.1)

H5b.1 is built (SRS H5b "As built (H5b.1)"). Where the shipped UI does not match the prototype:

- **Add note** makes a neutral quote mark with an empty paragraph after it, not a purple highlight (purple is "Disagree").
- **Previous / next mark** is not built; it moved to H5b.2 with subtree / all-notes export and the print view.
- **A box on the compiled PDF of an HTML paper** is saved without a section (no Marker blocks there); a text selection there finds its section by locating its words, and a few common words in text the paper's HTML lacks (a figure caption) are saved without a section, with a toast.
- **Marks on PDF pages** use the light palette in both themes, because the page itself is never inverted.
- **Show my marks** and **Box** sit in the reader bar beside the highlighter; the note card for a mark that is off screen stays open, pinned to the top of the gutter, with "The mark is off screen · Show".
- **The Notes page** is not built (H5b.2); its sketch here stays a sketch.

## Editor library — verified

Spike run 2026-11-16 on BlockNote **0.57.0** (React 19.2.6, Vitest + jsdom). The spike code was throwaway and is not committed. "Method" says how each finding was checked: **test** = a Vitest jsdom test against a mounted editor, **source** = reading the installed package.

**Install for Task 13** (all exact `0.57.0`, no XL package):

| Package | Licence | Notes |
| --- | --- | --- |
| `@blocknote/core` | MPL-2.0 | Includes the `./comments` entry (threads, `TipTapThreadStore`, comment mark). |
| `@blocknote/react` | MPL-2.0 | Peer `react ^18 \|\| ^19`; includes `createReactBlockSpec`, `createReactStyleSpec`, `useCreateBlockNote`, comment UI. |
| `@blocknote/shadcn` | MPL-2.0 | Peer `react ^18 \|\| ^19`, `tailwindcss ^4.1.12` (the app is on Tailwind 4); built on `@base-ui/react` (MIT), which the app already uses. |
| `@blocknote/code-block` | MPL-2.0 | Shiki highlighting (below). |

- **Licences (source):** each package's `LICENSE` file and `package.json` `license` are MPL-2.0. BlockNote's own README says the majority of BlockNote is MPL-2.0, and the **XL packages (`@blocknote/xl-*`) are GPL-3.0 or commercial**. Installing `@blocknote/xl-multi-column` showed `"GPL-3.0 OR PROPRIETARY"`: **rejected, never install any `@blocknote/xl-*`.** The whole resolved tree (`package-lock.json`, 4 BlockNote packages) contained no other GPL / commercial licence: MIT, ISC, Apache-2.0, BSD, MPL-2.0 (28), BlueOak, OFL-1.1, CC-BY-4.0, CC0, 0BSD. MPL-2.0 is file-level: we may not edit BlockNote's files without publishing those edits; wrapping and extending it from our own files is fine.
- **React 19 (source + test):** peer ranges accept 19; `BlockNoteView` from `@blocknote/shadcn` mounted and rendered under React 19.2.6 in jsdom. A harmless `act(...)` warning appears in tests (BlockNote updates state outside act).
- **`@blocknote/code-block` (source + test):** exists, MPL-2.0. It pulls `@shikijs/core`, `@shikijs/engine-javascript`, `@shikijs/langs-precompiled`, `@shikijs/themes` (all MIT, 4.6.0) and `prosemirror-highlight` (MIT). So no custom highlight.js block is needed. Wire it: `createCodeBlockSpec(codeBlockOptions)` under the block type **`codeBlock`** (not `code`) in `BlockNoteSchema`, and add the `syntaxHighlighter` extension. Language ids come from `codeBlockOptions.supportedLanguages` (about 50, including `python`, `latex`, `rust`, `sql`, `mermaid`); an unknown id (e.g. `py` as a block prop) throws "Language py is not supported", while typing ```` ```python ```` then a space creates a `codeBlock` with `language: "python"` (test).
- **Comments (source):** in the open core. `@blocknote/core/comments` (`CommentsExtension`, `ThreadStore`, `TipTapThreadStore`, comment mark) and `@blocknote/react` (`Comments`, `CommentEditor`, `CommentButton`) are both MPL-2.0. No fallback mark is needed; we implement our own `ThreadStore` backed by the PaperHub note API (the interface is pluggable). Not exercised end to end in this spike.
- **UI flavour:** `@blocknote/shadcn`. Its CSS is scoped to `.bn-shadcn` / `.bn-*` selectors and theme variables `--bn-colors-*`, so there is no global reset to fight with Tailwind 4 preflight; its only touches are `svg` sizing and link colour reverting inside `.bn-shadcn`. Map `--bn-colors-editor-background/text`, `--bn-colors-menu-*` to our theme tokens in the app CSS. `@blocknote/mantine` is not needed. (Method: source and a mounted render; a full dev-server visual pass is left to Task 13.)
- **Custom block (test):** works in a mounted editor; props round-trip unchanged.

  ```tsx
  const paperQuote = createReactBlockSpec(
    { type: "paperQuote",
      propSchema: { markId: { default: "" }, color: { default: "yellow" },
                    quote: { default: "" }, source: { default: "" } },
      content: "none" },
    { render: (p) => <blockquote>{p.block.props.quote}</blockquote> },
  );
  const schema = BlockNoteSchema.create({
    blockSpecs: { ...defaultBlockSpecs, paperQuote: paperQuote() }, // note the call
  });
  ```

  Inline-content blocks receive `contentRef` in render props. `createReactStyleSpec({ type: "hidden", propSchema: "boolean" }, { render })` defines a custom inline mark (used for `||hidden||`).
- **Input rules and shortcuts (test + source):**
  - `---` on an empty paragraph already becomes a `divider` block (built-in rule; test).
  - Block-level rules: an extension's `inputRules: [{ find: RegExp, replace: ({match, range, editor}) => PartialBlock | undefined }]` replaces the block (this is how ```` ```python ```` works). Rules are fixed at editor creation.
  - Inline marks need a tiptap extension: `{ key, tiptapExtensions: [Extension.create({ name, priority: 1000, addInputRules() { return [markInputRule({ find, type: this.editor.schema.marks.underline })] } })] }` passed in `extensions`. **Gotcha:** the built-in bold mark also matches `__x__`, so without `priority: 1000` `__under__` becomes bold; with it, `__under__` gives `underline` and `||sec||` gives the custom `hidden` style (both verified by typing through ProseMirror's `handleTextInput`).
  - Keyboard shortcuts: `keyboardShortcuts: { "Mod-Enter": ({editor, event}) => boolean }` on an extension.
- **Block ids (test):** `JSON.parse(JSON.stringify(editor.document))` -> `editor.replaceBlocks(editor.document, thatJson)` -> `editor.document` kept every `id`, including a nested child and a custom block (same id list, same order).
- **Tab / Shift+Tab (test):** with the caret in the second bullet item, a `Tab` keydown nested it under the first (`[a (1 child)]`); `Shift+Tab` brought it back to top level (`[a, b]`). Applies to any block, not only list items (core `Tab` / `Shift-Tab` handlers call nest / unnest). Inside a `codeBlock`, Tab indents the line (`indentLineWithTab`, source).
- **Not covered:** a real browser dev-server pass (typing feel, drag handle, slash menu rendering) and comments end to end; both belong to Task 13's live check.

## Files

| File | What it is |
| --- | --- |
| `h5b_prototype.html` | The prototype source, published as-is (the artifact host adds the document skeleton). |
