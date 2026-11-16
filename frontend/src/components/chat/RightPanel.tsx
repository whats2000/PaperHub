/** The chat's right panel: the reader (Citation Canvas), Slides, Memory and
 * Notes in one column. Reader or Slides share it with Notes — as tabs while
 * narrow, side by side from SIDE_BY_SIDE_MIN; Memory stays alone. */
import { Suspense, useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { PanelLoadBoundary } from "@/components/states/PanelLoadBoundary";
import { usePresence } from "@/hooks/usePresence";
import type { RightPanelState } from "@/hooks/useRightPanel";
import { retryableLazy } from "@/lib/retryableLazy";
import { cn } from "@/lib/utils";
import { useNotesPaneStore, type PaneTab, type PanelLayout } from "@/store/notesPane";

const NotesPane = retryableLazy(() =>
  import("@/components/notes/NotesPane").then((m) => ({
    default: m.NotesPane,
  })),
);

/** Exit time of a pane leaving the right panel (ms). */
const PANE_EXIT_MS = 200;
/** Tailwind v4 `translate-x-*` sets `translate`, not `transform`. */
const PANE_MOTION =
  "transition-[opacity,translate] duration-200 ease-out motion-reduce:transition-none";
const LEAVE = "animate-out fade-out-0 duration-200 fill-mode-forwards motion-reduce:animate-none";
const TABS: PaneTab[] = ["reader", "notes"];

const tabId = (tab: PaneTab) => `right-panel-tab-${tab}`;
const paneId = (tab: PaneTab) => `right-panel-pane-${tab}`;

interface Props {
  panel: RightPanelState;
  /** The Citation Canvas — always mounted so its documents stay loaded. */
  reader: ReactNode;
  /** Memory's content, rendered while Memory is open. */
  memory: ReactNode;
  /** The Slides panel, once mounted (kept mounted after its first open). */
  slides: ReactNode | null;
  resizeLabel: string;
  memoryLabel: string;
  slidesLabel: string;
}

export function RightPanel({ panel, reader, memory, slides, resizeLabel, memoryLabel, slidesLabel }: Props) {
  const { t } = useTranslation("notes");
  const { canvasOpen, slidesOpen, memoryOpen, notesOpen, primaryOpen, layout, notesWidth, behindTab } = panel;
  const closeNotes = useNotesPaneStore((s) => s.close);

  // Panes leave by sliding out (never by animating width): each stays shown
  // for its exit. The panel column collapses around them when all close.
  const notesPresence = usePresence(notesOpen, PANE_EXIT_MS);
  const canvasPresence = usePresence(canvasOpen, PANE_EXIT_MS);
  const slidesPresence = usePresence(slidesOpen, PANE_EXIT_MS);
  const primaryPresence = usePresence(primaryOpen, PANE_EXIT_MS);
  // A swap shows the incoming pane at once; only a pane closing alone lingers.
  const canvasShown = canvasOpen || (canvasPresence.mounted && !slidesOpen && !memoryOpen);
  const slidesShown = slidesOpen || (slidesPresence.mounted && !canvasOpen && !memoryOpen);
  // The layout the two had together, so a closing pane leaves from where it sat.
  const [sharedLayout, setSharedLayout] = useState<PanelLayout>(layout);
  if (notesOpen && primaryOpen && layout !== sharedLayout) setSharedLayout(layout);
  const notesLeaving = !notesOpen && notesPresence.mounted;
  const primaryLeaving = !primaryOpen && primaryPresence.mounted && notesOpen;
  const tabs = layout === "tabs";

  const hideProps = (hide: boolean) =>
    hide ? { inert: true, "aria-hidden": true as const } : {};
  const tabPanelProps = (tab: PaneTab) =>
    tabs ? { id: paneId(tab), role: "tabpanel", "aria-labelledby": tabId(tab) } : {};

  const primaryClass = cn(
    "h-full min-w-0",
    primaryLeaving
      ? cn("absolute inset-y-0 left-0 z-[1] slide-out-to-left-8", LEAVE)
      : tabs
        ? cn("absolute inset-0", PANE_MOTION, behindTab("reader") && "pointer-events-none -translate-x-3 opacity-0")
        : "relative flex-1",
    // Kept through its exit while the panel collapses around it.
    !primaryPresence.mounted && !memoryOpen && "hidden",
  );
  const notesClass = cn(
    "h-full min-w-0 bg-card",
    layout !== "side" && "border-l border-border",
    notesLeaving
      ? cn("absolute inset-y-0 right-0 z-[1] slide-out-to-right-8", LEAVE)
      : cn(
          "animate-in fade-in-0 slide-in-from-right-8 duration-200 ease-out motion-reduce:animate-none",
          tabs
            ? cn("absolute inset-0", PANE_MOTION, behindTab("notes") && "pointer-events-none translate-x-3 opacity-0")
            : layout === "side"
              ? "relative shrink-0"
              : "relative flex-1",
        ),
  );
  const notesStyle = notesLeaving
    ? { width: sharedLayout === "side" && primaryOpen ? notesWidth : "100%" }
    : layout === "side"
      ? { width: notesWidth }
      : undefined;
  const primaryStyle = primaryLeaving
    ? { width: sharedLayout === "side" ? panel.width - notesWidth : "100%" }
    : undefined;
  const primaryLabel = slidesOpen ? t("pane.slides", "Slides") : t("pane.reader", "Reader");

  const onTabKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const next = TABS[(TABS.indexOf(panel.activeTab) + step + TABS.length) % TABS.length] ?? "reader";
    panel.setActiveTab(next);
    e.currentTarget.querySelector<HTMLButtonElement>(`#${tabId(next)}`)?.focus();
  };

  return (
    <div className="relative min-h-0 overflow-hidden">
      {panel.open && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={resizeLabel}
          onPointerDown={panel.edge.onPointerDown}
          className="absolute left-0 top-0 z-10 h-full w-1.5 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
        />
      )}
      {/* Disable pointer events while dragging so the cursor can't enter
          cross-document iframes and swallow window pointermove events. */}
      <div className={cn("flex h-full flex-col", panel.dragging && "pointer-events-none")}>
        {/* Reader | Notes tabs while the two share a narrow panel: always
            mounted, the bar's height opens and closes. */}
        <div
          aria-hidden={!tabs || undefined}
          inert={!tabs ? true : undefined}
          className={cn(
            "grid shrink-0 transition-[grid-template-rows] duration-200 ease-out motion-reduce:transition-none",
            tabs ? "grid-rows-[1fr]" : "grid-rows-[0fr]",
          )}
        >
          <div className="overflow-hidden">
            <div
              role="tablist"
              aria-label={t("pane.tabsLabel", "Right panel")}
              className="flex gap-1 border-b border-l border-border bg-card px-2 py-1"
              onKeyDown={onTabKey}
            >
              {TABS.map((tab) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  id={tabId(tab)}
                  aria-controls={paneId(tab)}
                  aria-selected={panel.activeTab === tab}
                  tabIndex={panel.activeTab === tab ? 0 : -1}
                  onClick={() => panel.setActiveTab(tab)}
                  className={cn(
                    "rounded px-2 py-0.5 text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
                    panel.activeTab === tab
                      ? "bg-foreground/10 text-foreground"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                >
                  {tab === "reader" ? primaryLabel : t("pane.label", "Notes")}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="relative flex min-h-0 flex-1 overflow-hidden">
          <div
            className={primaryClass}
            style={primaryStyle}
            {...tabPanelProps("reader")}
            {...hideProps(primaryLeaving || behindTab("reader"))}
          >
            {/* Fix 2: CitationCanvas is ALWAYS mounted (never conditionally
                removed) so its fetched-document cache (iframes/PDF state)
                stays alive across open/close cycles. Hidden once closed —
                after its exit when it leaves alone — so closing Memory with
                the canvas closed does NOT reveal it. */}
            <div
              className="h-full w-full"
              hidden={!canvasShown}
              aria-hidden={!canvasOpen || undefined}
              {...(!canvasOpen ? { inert: true } : {})}
            >
              {reader}
            </div>

            {/* Memory Manager overlays the reader. With no backend session yet
                (empty chat) it shows global (user) memories only. */}
            {memoryOpen && (
              <div className="absolute inset-0 flex flex-col bg-card border-l border-border overflow-hidden">
                <div className="shrink-0 px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-border">
                  {memoryLabel}
                </div>
                <div className="flex-1 overflow-y-auto">{memory}</div>
              </div>
            )}

            {/* Slides overlay the reader. Kept MOUNTED once opened (hidden when
                closed) so its parsed + rasterized PDF survives a swap to the
                reader and back — no refetch, no "loading" flash. */}
            {slides !== null && (
              <div
                className="absolute inset-0 flex flex-col bg-card border-l border-border overflow-hidden"
                hidden={!slidesShown}
                aria-hidden={!slidesOpen || undefined}
                {...(!slidesOpen ? { inert: true } : {})}
              >
                <div className="shrink-0 px-3 py-2 text-xs font-semibold text-muted-foreground border-b border-border">
                  {slidesLabel}
                </div>
                <div className="flex-1 min-h-0 overflow-hidden">{slides}</div>
              </div>
            )}
          </div>

          {/* Side by side: the divider trades width between the reader and
              Notes; the panel's left edge sizes only the reader. */}
          {layout === "side" && !notesLeaving && (
            <div
              role="separator"
              aria-orientation="vertical"
              aria-label={t("pane.resize", "Resize Reader and Notes")}
              onPointerDown={panel.divider.onPointerDown}
              className="relative z-10 w-1.5 shrink-0 cursor-col-resize bg-border/40 transition-colors hover:bg-primary/40"
            />
          )}

          {notesPresence.mounted && (
            <div
              className={notesClass}
              style={notesStyle}
              {...tabPanelProps("notes")}
              {...hideProps(notesLeaving || behindTab("notes"))}
            >
              <PanelLoadBoundary onRetry={NotesPane.reset}>
                <Suspense fallback={null}>
                  <NotesPane onClose={closeNotes} />
                </Suspense>
              </PanelLoadBoundary>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
