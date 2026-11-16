/** The right panel's state: which panes are open, their widths and layout. */
import { useCanvasResize, useNotesDividerResize } from "@/hooks/useCanvasResize";
import { useCanvasStore } from "@/store/canvas";
import { rightPanelLayout, useNotesPaneStore, type PaneTab } from "@/store/notesPane";
import { useSlidesStore } from "@/store/slides";

export type RightPanelState = ReturnType<typeof useRightPanel>;

/** The panel's open panes, widths and layout. ChatPage sizes its grid column
 * and routes the composer toggles with it; `RightPanel` renders from it. */
export function useRightPanel(memoryOpen: boolean) {
  const canvasOpen = useCanvasStore((s) => s.open);
  const slidesOpen = useSlidesStore((s) => s.open);
  const notesOpen = useNotesPaneStore((s) => s.open);
  const activeTab = useNotesPaneStore((s) => s.activeTab);
  const setActiveTab = useNotesPaneStore((s) => s.setActiveTab);
  const storedNotesWidth = useNotesPaneStore((s) => s.notesWidth);
  const edge = useCanvasResize();
  const width = edge.width;
  const divider = useNotesDividerResize(width);

  const primaryOpen = canvasOpen || slidesOpen;
  const { layout, notesWidth } = rightPanelLayout({
    primaryOpen,
    notesOpen,
    panelWidth: width,
    notesWidth: storedNotesWidth,
  });
  return {
    open: canvasOpen || slidesOpen || memoryOpen || notesOpen,
    width,
    edge,
    divider,
    dragging: edge.resizing || divider.resizing,
    canvasOpen,
    slidesOpen,
    memoryOpen,
    notesOpen,
    primaryOpen,
    layout,
    notesWidth,
    activeTab,
    setActiveTab,
    /** A pane hidden behind the other tab. */
    behindTab: (tab: PaneTab) => layout === "tabs" && activeTab !== tab,
  };
}
