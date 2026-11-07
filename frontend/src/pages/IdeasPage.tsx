import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent, type ReactNode } from "react";
import { AlertCircle, BookOpen, LayoutGrid, Lightbulb, Loader2, Network, RotateCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { FilterChips } from "@/components/ideas/FilterChips";
import { GraphLegend } from "@/components/ideas/GraphLegend";
import { DocDomainEditor, DomainSettings } from "@/components/ideas/DomainEditor";
import { ExploreButton } from "@/components/ideas/ExploreButton";
import { IdeaDetail } from "@/components/ideas/IdeaDetail";
import { IdeaDrawer, type DrawerSlots } from "@/components/ideas/IdeaDrawer";
import { IdeaGraph } from "@/components/ideas/IdeaGraph";
import { IdeaKanban } from "@/components/ideas/IdeaKanban";
import { PaperReaderDrawer } from "@/components/ideas/PaperReaderDrawer";
import type { ReaderPaper } from "@/components/canvas/CitationCanvas";
import { ProblemEntry } from "@/components/ideas/ProblemEntry";
import { ProblemSwitcher } from "@/components/ideas/ProblemSwitcher";
import { ZoomControl } from "@/components/ideas/ZoomControl";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { usePanZoom, type ClientBox, type DrawingPoint, type Occluder, type ViewTransform } from "@/hooks/usePanZoom";
import { usePresence } from "@/hooks/usePresence";
import { DRAWER_WIDTH, layoutBounds, layoutItemBoxes, selectionAnchors, type GraphLayout } from "@/lib/ideaGraphLayout";
import { cn } from "@/lib/utils";
import {
  matchesFilter,
  useIdeationStore,
  type IdeationSelection,
  type IdeationView,
} from "@/store/ideation";
import type { CardProgress, LegendHighlight } from "@/types/ideation";

const ZOOM = { min: 0.5, max: 3 } as const;
const ZOOM_STEP = 1.25;

/** The legend's client box (bottom-left, over the graph), or undefined when it isn't laid out. */
function legendBox(svg: SVGSVGElement | null): ClientBox | undefined {
  const legend = svg?.parentElement?.querySelector<HTMLElement>('[data-testid="graph-legend"]');
  if (!legend) return undefined;
  const l = legend.getBoundingClientRect();
  return l.width > 0 && l.height > 0 ? { x0: l.left, y0: l.top, x1: l.right, y1: l.bottom } : undefined;
}

/** The legend as an occluder for "fit": no document may be framed under it. */
function legendOccluder(svg: SVGSVGElement | null, layout: GraphLayout): Occluder | undefined {
  const box = legendBox(svg);
  return box ? { box, points: layoutItemBoxes(layout) } : undefined;
}

const sameView = (a: ViewTransform, b: ViewTransform) =>
  Math.abs(a.k - b.k) < 1e-6 && Math.abs(a.x - b.x) < 0.5 && Math.abs(a.y - b.y) < 0.5;
/** A background click waits this long for a second click: a double-click zooms, it doesn't deselect. */
export const DESELECT_DELAY_MS = 250;

/** The graph element that stands for a selection (the drawer returns focus to it). */
function selectionElement(svg: SVGSVGElement | null, sel: NonNullable<IdeationSelection>): SVGElement | null {
  if (!svg) return null;
  const id =
    sel.kind === "idea"
      ? `idea-${sel.id}`
      : sel.kind === "doc"
        ? `node-${sel.id}`
        : sel.kind === "domain"
          ? `hull-label-${sel.id}`
          : "problem-node";
  return svg.querySelector<SVGElement>(`[data-testid="${id}"]`) ?? svg;
}

// Enter/exit for the in-place states: a 150 ms fade.
const FADE =
  "duration-150 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 motion-reduce:animate-none";

function Overlay({ open, children }: { open: boolean; children: ReactNode }) {
  const { mounted, state } = usePresence(open);
  if (!mounted) return null;
  return (
    <div
      data-state={state}
      className={cn(
        "absolute inset-0 z-10 flex flex-col items-center justify-center gap-2 bg-background/80 text-center",
        FADE,
      )}
    >
      {children}
    </div>
  );
}

/** A compact note in the graph's top-left corner: it drops in from the top edge and rises back out. */
function EmptyHint({ open, children }: { open: boolean; children: ReactNode }) {
  const { mounted, state } = usePresence(open);
  if (!mounted) return null;
  return (
    <div
      data-testid="ideas-empty-hint"
      data-state={state}
      className={cn(
        "pointer-events-none absolute top-3 left-3.5 z-[6] w-[min(17rem,calc(100%-2rem))]",
        "rounded-md border bg-popover px-3 py-2 text-xs text-muted-foreground shadow-md",
        "duration-200 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:slide-in-from-top-2",
        "data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:slide-out-to-top-2 motion-reduce:animate-none",
      )}
    >
      {children}
    </div>
  );
}

/** The card phase is under way: some documents still lack a processed card. */
function buildingCards(p: CardProgress | null | undefined): p is CardProgress & { cards_total: number } {
  return !!p && typeof p.cards_total === "number" && p.cards_total > 0 && (p.cards_ready ?? 0) < p.cards_total;
}

/** "N documents couldn't be analysed" under the filter row; opens and closes its height. */
function SkippedNote({ count }: { count: number }) {
  const { t } = useTranslation("ideas");
  // Keep the last count while the note collapses.
  const [shown, setShown] = useState(count);
  if (count > 0 && count !== shown) setShown(count);
  return (
    <Collapsible open={count > 0}>
      <p data-testid="ideas-skipped" className="px-5 pt-1 text-xs text-muted-foreground">
        {t("state.skipped", "{{count}} documents couldn't be analysed", { count: shown })}
      </p>
    </Collapsible>
  );
}

/** One view's slot. Both stay mounted; the inactive one slides out in its tab's direction. */
function ViewSlot({ view, active, children }: { view: IdeationView; active: boolean; children?: ReactNode }) {
  const away = view === "graph" ? "-translate-x-6" : "translate-x-6";
  return (
    <div
      data-testid={`ideas-${view}-view`}
      inert={!active}
      aria-hidden={active ? undefined : true}
      className={cn(
        "absolute inset-0 flex min-h-0 flex-col transition-[opacity,transform] duration-200 ease-out motion-reduce:transition-none",
        active ? "translate-x-0 opacity-100" : cn(away, "pointer-events-none opacity-0"),
      )}
    >
      {children}
    </div>
  );
}

export function IdeasPage() {
  const { t } = useTranslation(["ideas", "states"]);
  const entryRef = useRef<HTMLTextAreaElement>(null);
  const load = useIdeationStore((s) => s.load);
  const stopPolling = useIdeationStore((s) => s.stopPolling);
  const view = useIdeationStore((s) => s.view);
  const setView = useIdeationStore((s) => s.setView);
  const graph = useIdeationStore((s) => s.graph);
  const loading = useIdeationStore((s) => s.loading);
  const error = useIdeationStore((s) => s.error);
  const scope = useIdeationStore((s) => s.scope);
  const libraryDocs = useIdeationStore((s) => s.libraryDocs);
  const libraryPrivate = useIdeationStore((s) => s.libraryPrivate);
  const filter = useIdeationStore((s) => s.filter);
  const selected = useIdeationStore((s) => s.selected);
  const setSelected = useIdeationStore((s) => s.setSelected);
  const leavingIdeaIds = useIdeationStore((s) => s.leavingIdeaIds);
  const visibleIdeas = useMemo(
    () => graph?.ideas.filter((i) => matchesFilter(i.status, filter)) ?? [],
    [graph, filter],
  );
  // The graph keeps drawing a just-dismissed idea while it fades out.
  const graphIdeas = useMemo(
    () =>
      leavingIdeaIds.length === 0
        ? visibleIdeas
        : (graph?.ideas.filter((i) => matchesFilter(i.status, filter) || leavingIdeaIds.includes(i.id)) ?? []),
    [graph, filter, visibleIdeas, leavingIdeaIds],
  );
  const problem = useIdeationStore((s) =>
    s.scope === "explore" ? null : s.problems.find((p) => p.id === s.scope) ?? null,
  );

  useEffect(() => {
    void load();
    return stopPolling;
  }, [load, stopPolling]);

  // Pan / zoom and the detail drawer. Nothing is selected on entry.
  const svgRef = useRef<SVGSVGElement>(null);
  const pz = usePanZoom(svgRef, ZOOM);
  const { fit, wasDrag } = pz;
  const layoutRef = useRef<GraphLayout | null>(null);
  const drawerOpenRef = useRef(false);
  // The current view, for comparing with the last automatic fit.
  const viewRef = useRef<ViewTransform>(pz.transform);
  const scopeRef = useRef(scope);
  // The last automatic fit: the scope and documents it framed, and the view it set.
  const autoFit = useRef<{ scope: typeof scope; docs: string; view: ViewTransform } | null>(null);
  const onLayout = useCallback(
    (l: GraphLayout) => {
      layoutRef.current = l;
      const docs = l.nodes.map((n) => n.id).join(",");
      const last = autoFit.current?.scope === scopeRef.current ? autoFit.current : null;
      // Frame each scope's first drawing; re-frame when its documents change,
      // unless the reader has moved the view since the last automatic fit.
      if (last && (last.docs === docs || !sameView(last.view, viewRef.current))) return;
      const b = layoutBounds(l);
      if (!b) return;
      // A new scope appears already framed; arriving documents glide into frame.
      const view = fit(b, drawerOpenRef.current ? DRAWER_WIDTH : 0, !last, legendOccluder(svgRef.current, l));
      autoFit.current = { scope: scopeRef.current, docs, view };
    },
    [fit],
  );
  // A press that turned into a pan is never a selection or a deselect click.
  const select = useCallback(
    (sel: IdeationSelection) => {
      if (!wasDrag()) setSelected(sel);
    },
    [wasDrag, setSelected],
  );
  const selectDomain = useCallback((id: number) => select({ kind: "domain", id }), [select]);
  // A background click deselects after a short wait; the second click of a
  // double-click (detail 2) cancels it, so a double-click only zooms.
  const deselectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelDeselect = useCallback(() => {
    if (deselectTimer.current !== null) {
      clearTimeout(deselectTimer.current);
      deselectTimer.current = null;
    }
  }, []);
  const deselect = useCallback(
    (e: MouseEvent<SVGSVGElement>) => {
      cancelDeselect();
      if (wasDrag() || e.detail >= 2) return;
      deselectTimer.current = setTimeout(() => {
        deselectTimer.current = null;
        setSelected(null);
      }, DESELECT_DELAY_MS);
    },
    [wasDrag, cancelDeselect, setSelected],
  );
  // A newer selection (or leaving the page) cancels a pending deselect.
  useEffect(() => cancelDeselect, [selected, cancelDeselect]);
  // The cards view has no svg mounted: hand focus back to the idea's card (or the board).
  const bodyRef = useRef<HTMLDivElement>(null);
  const returnFocusTo = useCallback(
    (sel: NonNullable<IdeationSelection>) => {
      if (view === "cards") {
        const board = bodyRef.current?.querySelector<HTMLElement>('[data-testid="idea-kanban"]') ?? null;
        const card =
          sel.kind === "idea"
            ? (board?.querySelector<HTMLElement>(`[data-testid="idea-card-${sel.id}"]:not([data-leaving])`) ?? null)
            : null;
        return card ?? board;
      }
      return selectionElement(svgRef.current, sel);
    },
    [view],
  );
  const anchorsFor = useCallback(
    (sel: NonNullable<IdeationSelection>) => selectionAnchors(layoutRef.current, graph?.ideas ?? [], sel),
    [graph],
  );
  // The selection's auto-pan keeps it out from under the legend too.
  const { panToKeepVisible } = pz;
  const keepVisible = useCallback(
    (pts: readonly DrawingPoint[], rightInset: number) => panToKeepVisible(pts, rightInset, legendBox(svgRef.current)),
    [panToKeepVisible],
  );
  const drawerOpen = selected !== null;
  // The legend category under the pointer, lit on the graph while hovered.
  const [legendHighlight, setLegendHighlight] = useState<LegendHighlight | null>(null);
  // The document open in the paper reader (over the graph and the drawer).
  const [readerPaper, setReaderPaper] = useState<ReaderPaper | null>(null);
  const closeReader = useCallback(() => setReaderPaper(null), []);
  // Mirrors for onLayout, which runs from the graph and must not change identity.
  useEffect(() => {
    viewRef.current = pz.transform;
    scopeRef.current = scope;
    drawerOpenRef.current = drawerOpen;
  });
  const domains = graph?.domains;
  const slots = useMemo<DrawerSlots>(
    () => ({
      idea: (idea) => <IdeaDetail key={idea.id} idea={idea} />,
      doc: (doc) => (
        <>
          <Button
            variant="outline"
            size="sm"
            className="mb-3"
            onClick={() => setReaderPaper({ paper_content_id: doc.id, title: doc.title })}
          >
            <BookOpen aria-hidden="true" />
            {t("ideas:reader.open", "Read paper")}
          </Button>
          <DocDomainEditor
            doc={doc}
            domains={domains ?? []}
            onOpenDomain={(id) => setSelected({ kind: "domain", id })}
          />
        </>
      ),
      domain: (domain) => (
        <DomainSettings
          key={`${domain.id}:${domain.name}`}
          domain={domain}
          domains={domains ?? []}
          onMerged={(id) => setSelected({ kind: "domain", id })}
        />
      ),
    }),
    [domains, setSelected, t],
  );
  const fitAll = useCallback(() => {
    const l = layoutRef.current;
    const b = l ? layoutBounds(l) : null;
    fit(b ?? undefined, drawerOpen ? DRAWER_WIDTH : 0, false, l ? legendOccluder(svgRef.current, l) : undefined);
  }, [fit, drawerOpen]);

  const exploring = useIdeationStore((s) => s.scope === "explore" && s.exploring);
  const exploreStatus = useIdeationStore((s) => s.exploreStatus);
  const retryProblem = useIdeationStore((s) => s.retryProblem);
  const generating = problem?.status === "generating" || exploring;
  const failed = problem?.status === "error";
  const [retrying, setRetrying] = useState(false);
  const retry = async () => {
    if (!problem) return;
    setRetrying(true);
    try {
      await retryProblem(problem.id);
    } catch {
      toast.error(t("ideas:state.retryFailed", "Couldn't retry this problem"));
    } finally {
      setRetrying(false);
    }
  };
  const progress = exploring ? exploreStatus : problem;
  const skipped =
    generating ? 0 : ((scope === "explore" ? exploreStatus?.skipped_docs : problem?.skipped_docs) ?? 0);
  const outcome = scope === "explore" ? exploreStatus : problem?.status === "ready" ? problem : null;
  const noRelevant = outcome?.nothing_relevant === true;
  const empty = !!graph && !loading && !generating && !failed && !error && graph.ideas.length === 0;
  const generatingText = exploring
    ? t("ideas:explore.overlay", "Finding cross-domain pairs in your library…")
    : t("ideas:state.generating", "Finding cross-domain ideas for your problem…");

  return (
    <div className="flex flex-1 min-h-0 flex-col animate-in fade-in-0 duration-150 motion-reduce:animate-none">
      <PageHeader
        icon={Lightbulb}
        title={t("states:nav.ideas", "Ideas")}
        meta={
          libraryDocs === null
            ? undefined
            : libraryPrivate
              ? t("ideas:header.libraryPrivate", "Library: {{count}} documents ({{private}} private)", {
                  count: libraryDocs,
                  private: libraryPrivate,
                })
              : t("ideas:header.library", "Library: {{count}} documents", { count: libraryDocs })
        }
        actions={
          <Tabs value={view} onValueChange={(v) => setView(v as IdeationView)} className="gap-0">
            <TabsList className="h-8 w-auto" aria-label={t("ideas:view.aria", "View")}>
              <TabsTrigger value="graph" className="gap-1.5 text-xs">
                <Network className="h-3.5 w-3.5" aria-hidden="true" />
                {t("ideas:view.graph", "Graph")}
              </TabsTrigger>
              <TabsTrigger value="cards" className="gap-1.5 text-xs">
                <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
                {t("ideas:view.cards", "Cards")}
              </TabsTrigger>
            </TabsList>
          </Tabs>
        }
      />
      <div className="flex gap-2 px-5 pt-3 pb-1">
        <ProblemSwitcher entryRef={entryRef} />
        <ProblemEntry inputRef={entryRef} />
      </div>
      <div className="flex items-start">
        <FilterChips />
        <ExploreButton />
      </div>
      <SkippedNote count={skipped} />
      {/* The graph / cards region (Tasks 9–11 mount their views in the slots). */}
      <div ref={bodyRef} data-testid="ideas-body" data-scope={String(scope)} className="relative mt-2 flex flex-1 min-h-0 overflow-hidden">
        <ViewSlot view="graph" active={view === "graph"}>
          {graph && graph.documents.length > 0 && (
            <>
              <IdeaGraph
                graph={graph}
                ideas={graphIdeas}
                leavingIds={leavingIdeaIds}
                highlight={legendHighlight}
                selected={selected}
                onSelect={select}
                onSelectDomain={selectDomain}
                onBackgroundClick={deselect}
                svgRef={svgRef}
                viewTransform={pz.transform}
                viewLive={pz.live}
                onLayout={onLayout}
                className="animate-in fade-in-0 duration-200 motion-reduce:animate-none"
              />
              <ZoomControl
                k={pz.transform.k}
                min={ZOOM.min}
                max={ZOOM.max}
                onZoomIn={() => pz.zoomAt(ZOOM_STEP)}
                onZoomOut={() => pz.zoomAt(1 / ZOOM_STEP)}
                onFit={fitAll}
                shifted={drawerOpen}
              />
              <GraphLegend domains={graph.domains} onHighlight={setLegendHighlight} />
            </>
          )}
        </ViewSlot>
        <ViewSlot view="cards" active={view === "cards"}>
          {graph && (
            <IdeaKanban
              graph={graph}
              ideas={visibleIdeas}
              filter={filter}
              selected={selected}
              onSelect={setSelected}
              rightInset={drawerOpen ? DRAWER_WIDTH : 0}
            />
          )}
        </ViewSlot>
        {/* Over both views, sliding in from the right; it never squeezes them. */}
        <IdeaDrawer
          graph={graph}
          ideas={visibleIdeas}
          selected={selected}
          onSelect={setSelected}
          panToKeepVisible={view === "graph" ? keepVisible : undefined}
          anchorsFor={anchorsFor}
          returnFocusTo={returnFocusTo}
          slots={slots}
        />
        <PaperReaderDrawer paper={readerPaper} onClose={closeReader} />
        {/* Always mounted, so screen readers hear the text when it's filled in. */}
        <p role="status" className="sr-only">
          {generating && !error ? generatingText : ""}
        </p>
        <Overlay open={generating && !error}>
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden="true" />
          <p className="text-sm font-medium">{generatingText}</p>
          <p data-testid="ideas-generating-sub" className="text-xs text-muted-foreground">
            {buildingCards(progress)
              ? t("ideas:state.summarising", "Summarising your documents — {{ready}}/{{total}}", {
                  ready: progress.cards_ready ?? 0,
                  total: progress.cards_total,
                })
              : t(
                  "ideas:state.generatingSub",
                  "Reading the mechanism cards of your library and pairing them across domains.",
                )}
          </p>
        </Overlay>
        <Overlay open={failed}>
          <AlertCircle className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          <p role="alert" className="text-sm">
            {t("ideas:state.failed", "Generating ideas for this problem failed.")}
          </p>
          <Button variant="outline" size="sm" disabled={retrying} onClick={() => void retry()}>
            <RotateCw aria-hidden="true" />
            {t("ideas:state.retry", "Retry")}
          </Button>
        </Overlay>
        <Overlay open={!!error && !failed}>
          <AlertCircle className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
          <p role="alert" className="text-sm">
            {t("ideas:state.loadFailed", "Couldn't load ideas.")}
          </p>
          <Button variant="outline" size="sm" onClick={() => void load()}>
            <RotateCw aria-hidden="true" />
            {t("ideas:state.retry", "Retry")}
          </Button>
        </Overlay>
        {/* No ideas yet: a small hint over the graph, which stays readable and clickable. */}
        <EmptyHint open={empty && (view === "graph" || noRelevant)}>
          <p className="font-medium text-foreground">
            {noRelevant
              ? t("ideas:state.noRelevant", "Nothing in your library is relevant to this problem.")
              : scope === "explore"
                ? t("ideas:state.emptyExplore", "No cross-domain pairs yet.")
                : t("ideas:state.emptyProblem", "No ideas for this problem yet.")}
          </p>
          {noRelevant && (
            <p data-testid="ideas-no-relevant">
              {outcome?.nothing_relevant_reason ? `${outcome.nothing_relevant_reason} ` : ""}
              {t(
                "ideas:state.noRelevantHint",
                "Add papers on this topic to your library, or describe the problem differently, then try again.",
              )}
            </p>
          )}
          {!noRelevant && graph && graph.documents.length > 0 && (
            <p>{t("ideas:state.emptyHint", "Documents are grouped by domain. Click one to see or edit its domains.")}</p>
          )}
        </EmptyHint>
      </div>
    </div>
  );
}
