/**
 * Marks the page (`html[data-dragging]`) while an HTML drag is in progress;
 * index.css stops the page itself from scrolling then. BlockNote's drag
 * image for a table row or column is a child of <body> whose 24px line made
 * the page taller than the window, so window scrollbars came and went with
 * every drag. Outside a drag the page scrolls as before (a narrow window
 * still reaches everything).
 *
 * The mark outlasts the drag image: BlockNote removes it on the handle's
 * dragend, which for a row arrives ~100ms after the drop (the handle moves
 * with its row). So once the drag ends (drop, dragend, or the first pointer
 * move after it, as no pointer events fire during a drag and dragend never
 * reaches the document when its source was removed mid-drag) the mark stays
 * until <body> is back to the children it had at dragstart, or `settleMs`
 * has passed.
 *
 * Returns a function that removes the listeners.
 */
export function installDragScrollGuard(
  doc: Document,
  { settleMs = 1000 }: { settleMs?: number } = {},
): () => void {
  const root = doc.documentElement;
  let baseline = 0;
  let observer: MutationObserver | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const stopWaiting = () => {
    observer?.disconnect();
    observer = undefined;
    if (timer !== undefined) clearTimeout(timer);
    timer = undefined;
  };
  const clear = () => {
    stopWaiting();
    root.removeAttribute("data-dragging");
  };
  const settled = () => doc.body.childElementCount <= baseline;

  // Capture phase: runs before the drag source's own dragstart handler, so
  // the baseline is counted before the drag image is appended.
  const start = () => {
    stopWaiting();
    baseline = doc.body.childElementCount;
    root.setAttribute("data-dragging", "");
  };
  const end = () => {
    if (!root.hasAttribute("data-dragging") || observer) return;
    if (settled()) {
      clear();
      return;
    }
    observer = new MutationObserver(() => {
      if (settled()) clear();
    });
    observer.observe(doc.body, { childList: true });
    timer = setTimeout(clear, settleMs);
  };

  const ends = ["dragend", "drop", "pointermove"] as const;
  doc.addEventListener("dragstart", start, true);
  for (const type of ends) doc.addEventListener(type, end, true);
  return () => {
    stopWaiting();
    doc.removeEventListener("dragstart", start, true);
    for (const type of ends) doc.removeEventListener(type, end, true);
  };
}
