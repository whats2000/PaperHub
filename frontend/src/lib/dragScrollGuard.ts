/**
 * Marks the page (`html[data-dragging]`) while an HTML drag is in progress;
 * index.css stops the page itself from scrolling then. BlockNote's drag
 * image for a table row or column is a child of <body> whose 24px line made
 * the page taller than the window, so window scrollbars came and went with
 * every drag. Outside a drag the page scrolls as before (a narrow window
 * still reaches everything).
 *
 * Returns a function that removes the listeners.
 */
export function installDragScrollGuard(doc: Document): () => void {
  const root = doc.documentElement;
  const start = () => root.setAttribute("data-dragging", "");
  const end = () => root.removeAttribute("data-dragging");
  // No pointer events fire during a drag, so the first one after it ends
  // also clears the mark when dragend never reached the document (its
  // source was removed mid-drag).
  const ends = ["dragend", "drop", "pointermove"] as const;
  doc.addEventListener("dragstart", start, true);
  for (const type of ends) doc.addEventListener(type, end, true);
  return () => {
    doc.removeEventListener("dragstart", start, true);
    for (const type of ends) doc.removeEventListener(type, end, true);
  };
}
