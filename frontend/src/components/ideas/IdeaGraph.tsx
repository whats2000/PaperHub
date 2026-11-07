import {
  memo,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  type Ref,
} from "react";
import { Check, EyeOff, FlaskConical, Lock, Star, X, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { ViewTransform } from "@/hooks/usePanZoom";
import { useTweenedLayout } from "@/hooks/useTweenedLayout";
import { assignDomainColors, domainColorVar } from "@/lib/domainColors";
import { svgSafeId } from "@/lib/svgId";
import {
  domainHulls,
  hullAdjacency,
  LABEL_DY,
  layoutGraph,
  PROBLEM_HALF,
  placeLabels,
  SHORT_LABEL_CHARS,
  truncateLabel,
  truncateToWidth,
  WARM,
  type GraphLayout,
  type LayoutNode,
} from "@/lib/ideaGraphLayout";
import { cn } from "@/lib/utils";
import type { IdeationSelection } from "@/store/ideation";
import type { GraphResponse, Idea, IdeaStatus } from "@/types/ideation";

/** Line style per status: status is never encoded by colour. */
const STATUS_DASH: Record<IdeaStatus, string | undefined> = {
  new: "5 4",
  saved: undefined,
  trying: undefined,
  worked: undefined,
  failed: "1.5 4",
  dismissed: "1.5 4",
};
const STATUS_OPACITY: Record<IdeaStatus, number> = {
  new: 0.6,
  saved: 0.8,
  trying: 0.8,
  worked: 0.8,
  failed: 0.4,
  dismissed: 0.4,
};
const STATUS_ICON: Partial<Record<IdeaStatus, LucideIcon>> = {
  saved: Star,
  trying: FlaskConical,
  worked: Check,
  failed: X,
  dismissed: EyeOff,
};
const STATUS_FALLBACK: Record<IdeaStatus, string> = {
  new: "New",
  saved: "Saved",
  trying: "Trying",
  worked: "Worked",
  failed: "Failed",
  dismissed: "Dismissed",
};

const DIM = 0.3;
const FADE = "transition-opacity duration-200 ease-out motion-reduce:transition-none";
/** Keyboard focus: a focused element is never dimmed. */
const FOCUSABLE = "outline-none focus-visible:opacity-100";
/** The selection-style ring that shows only while its group has keyboard focus. */
const FOCUS_RING =
  "opacity-0 transition-opacity duration-150 group-focus-visible:opacity-100 motion-reduce:transition-none";
/** Line opacities that get their own arrowhead, so the head matches its line. */
const MARKER_OPACITIES = [...new Set(Object.values(STATUS_OPACITY))];
const markerId = (uid: string, opacity: number) => `${uid}-arrow-${Math.round(opacity * 100)}`;

/** var()-based paint goes in `style`: presentation attributes don't take var() everywhere. */
function paint(fill?: string, stroke?: string, extra?: CSSProperties): CSSProperties {
  return { ...(fill !== undefined && { fill }), ...(stroke !== undefined && { stroke }), ...extra };
}

/** The layout canvas grows with the library so ~60 documents keep room to breathe. */
function canvasSize(docCount: number): { width: number; height: number } {
  const width = Math.max(900, Math.round(Math.sqrt(Math.max(1, docCount)) * 150));
  return { width, height: Math.round(width * 0.66) };
}

/** Path for the half of a circle on the left (`side=0`) or right (`side=1`). */
function halfCircle(x: number, y: number, r: number, side: 0 | 1): string {
  return `M${x},${y - r} A${r},${r} 0 0 ${side} ${x},${y + r} Z`;
}

function activate(e: KeyboardEvent, fn: () => void) {
  if (e.key === "Enter" || e.key === " ") {
    e.preventDefault();
    fn();
  }
}

interface Focus {
  docs: Set<number>;
  ideas: Set<number>;
  problem: boolean;
}

function focusOf(selected: IdeationSelection, ideas: Idea[], documents: GraphResponse["documents"]): Focus | null {
  if (!selected) return null;
  const f: Focus = { docs: new Set(), ideas: new Set(), problem: false };
  const take = (idea: Idea) => {
    f.ideas.add(idea.id);
    f.docs.add(idea.source_doc);
    if (idea.target_doc === null) f.problem = true;
    else f.docs.add(idea.target_doc);
  };
  if (selected.kind === "idea") {
    const idea = ideas.find((i) => i.id === selected.id);
    // A selected idea hidden by the filter dims nothing.
    if (!idea) return null;
    take(idea);
  } else if (selected.kind === "doc") {
    f.docs.add(selected.id);
    ideas.filter((i) => i.source_doc === selected.id || i.target_doc === selected.id).forEach(take);
  } else if (selected.kind === "domain") {
    // A domain focuses its own documents only; their ideas stay at full opacity.
    for (const d of documents) if (d.domains.includes(selected.id)) f.docs.add(d.id);
    ideas.filter((i) => f.docs.has(i.source_doc) || (i.target_doc !== null && f.docs.has(i.target_doc))).forEach((i) => f.ideas.add(i.id));
  } else {
    f.problem = true;
    ideas.filter((i) => i.target_doc === null).forEach(take);
  }
  return f;
}

export interface IdeaGraphProps {
  graph: GraphResponse;
  /** Ideas to draw (already filtered); defaults to every idea in the graph. */
  ideas?: Idea[];
  selected?: IdeationSelection;
  onSelect?: (selection: IdeationSelection) => void;
  onSelectDomain?: (domainId: number) => void;
  /** Viewport transform from pan/zoom, applied to the drawing. */
  viewTransform?: ViewTransform;
  /** The transform is following a live gesture: apply it without the glide. */
  viewLive?: boolean;
  svgRef?: Ref<SVGSVGElement>;
  /** Called with each new target layout (not the tweened frames), for auto-pan and fit. */
  onLayout?: (layout: GraphLayout) => void;
  /** A click that reached the svg background (not a node, idea or label). */
  onBackgroundClick?: (e: MouseEvent<SVGSVGElement>) => void;
  /** Ideas fading out (just dismissed): drawn at opacity 0 until they're dropped. */
  leavingIds?: readonly number[];
  seed?: number;
  className?: string;
}

function IdeaGraphImpl({
  graph,
  ideas = graph.ideas,
  selected = null,
  onSelect,
  onSelectDomain,
  viewTransform,
  viewLive = false,
  svgRef,
  onLayout,
  onBackgroundClick,
  leavingIds,
  seed = 1,
  className,
}: IdeaGraphProps) {
  const { t } = useTranslation("ideas");
  const uid = svgSafeId(useId());
  // The keyboard-focused idea: its line goes full opacity, so its head must too.
  const [kbIdea, setKbIdea] = useState<number | null>(null);
  // Set by a press, so the focus that a click causes isn't taken for keyboard focus.
  const pressing = useRef(false);
  const { width, height } = canvasSize(graph.documents.length);

  // Two-stage layout, both pure and seeded:
  // 1. `base` depends only on the structure (documents, their domains, the
  //    problem) and is laid out cold with every node at the zero-idea size.
  // 2. `layout` warm-starts from `base` with the real idea counts. While ideas
  //    stream in, only stage 2 reruns, so nodes stay near where they were.
  const structureKey = JSON.stringify([
    graph.documents.map((d) => [d.id, d.domains]),
    graph.domains.map((d) => d.id),
    graph.problem !== null,
    seed,
    width,
  ]);
  const countsKey = JSON.stringify(graph.documents.map((d) => d.idea_count));
  const base = useMemo(
    () =>
      layoutGraph(
        { ...graph, documents: graph.documents.map((d) => ({ ...d, idea_count: 0 })) },
        { width, height, seed },
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [structureKey],
  );
  const layout = useMemo(
    () =>
      layoutGraph(graph, {
        width,
        height,
        seed,
        initial: new Map(base.nodes.map((n) => [n.id, n])),
        ticks: WARM.ticks,
        alpha: WARM.alpha,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [base, countsKey],
  );
  useEffect(() => {
    onLayout?.(layout);
  }, [layout, onLayout]);
  const px = layout.problem?.x;
  const py = layout.problem?.y;
  const hullsFor = useCallback(
    (nodes: readonly LayoutNode[]) =>
      domainHulls(graph, nodes, px === undefined || py === undefined ? null : { x: px, y: py }),
    [graph, px, py],
  );
  // Glide between layouts; edges and hulls follow the tweened positions.
  const shown = useTweenedLayout(layout, hullsFor);

  const colorIndex = useMemo(() => {
    const out = new Map<number, number | null>();
    // The server owns color_index; a domain that arrives without one (undefined)
    // gets the client-side mirror of the same rule, from the drawn hulls.
    const missing = graph.domains.some((d) => d.color_index === undefined);
    const fallback = missing ? assignDomainColors(graph.domains, hullAdjacency(layout.hulls)) : null;
    for (const d of graph.domains) {
      out.set(d.id, d.color_index !== undefined ? d.color_index : (fallback?.get(d.id) ?? null));
    }
    return out;
  }, [graph.domains, layout.hulls]);
  const colorOf = (domainId: number | undefined) =>
    domainColorVar(domainId === undefined ? null : (colorIndex.get(domainId) ?? null));

  const nodeById = useMemo(() => new Map(shown.nodes.map((n) => [n.id, n])), [shown.nodes]);
  const docById = useMemo(() => new Map(graph.documents.map((d) => [d.id, d])), [graph.documents]);
  const domainName = useMemo(() => new Map(graph.domains.map((d) => [d.id, d.name])), [graph.domains]);
  const focus = useMemo(() => focusOf(selected, ideas, graph.documents), [selected, ideas, graph.documents]);
  const selIdea = selected?.kind === "idea" ? ideas.find((i) => i.id === selected.id) : undefined;
  const problemPos = shown.problem;
  // Document labels: collision-aware at the current zoom (see `placeLabels`).
  const k = viewTransform?.k ?? 1;
  const labelScale = 1 / Math.max(k, 1);
  const labels = useMemo(
    () =>
      placeLabels(
        shown.nodes.flatMap((n) => {
          const doc = docById.get(n.id);
          if (!doc) return [];
          const related = !!focus?.docs.has(n.id);
          const pinned =
            (selected?.kind === "doc" && selected.id === n.id) ||
            selIdea?.source_doc === n.id ||
            selIdea?.target_doc === n.id;
          return [{ id: n.id, x: n.x, y: n.y, r: n.r, title: doc.title, priority: (related ? 1000 : 0) + doc.idea_count, pinned }];
        }),
        k,
      ),
    [shown.nodes, docById, focus, selected, selIdea, k],
  );
  const dimDoc = (id: number) => (focus && !focus.docs.has(id) ? DIM : 1);

  const statusLabel = (s: IdeaStatus) => t(`ideas:graph.status.${s}`, STATUS_FALLBACK[s]);
  const docTitle = (id: number | null) =>
    id === null ? t("ideas:graph.problem", "Your problem") : (docById.get(id)?.title ?? "");

  // --- hulls ---
  const hulls = shown.hulls.map((h) => {
    // The layout places the label outside the hull edge, clear of other labels and documents.
    const xs = h.points.map((p) => p[0]);
    const labelX = h.label?.x ?? (Math.min(...xs) + Math.max(...xs)) / 2;
    const labelY = h.label?.y ?? Math.min(...h.points.map((p) => p[1])) + 16;
    const color = colorOf(h.domainId);
    const dimmed =
      focus && !graph.documents.some((d) => focus.docs.has(d.id) && d.domains.includes(h.domainId));
    const name = domainName.get(h.domainId) ?? "";
    const pick = onSelectDomain ? () => onSelectDomain(h.domainId) : null;
    return (
      <g key={h.domainId} data-testid={`hull-${h.domainId}`} className={FADE} opacity={dimmed ? DIM : 1}>
        <polygon
          points={h.points.map((p) => p.join(",")).join(" ")}
          style={paint(color, color, { fillOpacity: "var(--hull-alpha)" })}
          strokeOpacity={0.55}
          strokeWidth={1.2}
          strokeLinejoin="round"
        />
        <text
          data-testid={`hull-label-${h.domainId}`}
          x={labelX}
          y={labelY}
          textAnchor="middle"
          fontSize={11.5}
          fontWeight={600}
          style={paint("var(--foreground)", "var(--background)")}
          strokeWidth={3}
          paintOrder="stroke"
          className={cn(pick && "cursor-pointer outline-none hover:underline focus-visible:underline")}
          role={pick ? "button" : undefined}
          tabIndex={pick ? 0 : undefined}
          onClick={
            pick
              ? (e: MouseEvent) => {
                  e.stopPropagation();
                  pick();
                }
              : undefined
          }
          onKeyDown={pick ? (e: KeyboardEvent) => activate(e, pick) : undefined}
        >
          {name}
        </text>
      </g>
    );
  });

  // --- citation hairlines ---
  const citations = graph.citations.map((c) => {
    const a = nodeById.get(c.source_id);
    const b = nodeById.get(c.target_id);
    if (!a || !b) return null;
    const on = !focus || (focus.docs.has(c.source_id) && focus.docs.has(c.target_id));
    return (
      <line
        key={`${c.source_id}-${c.target_id}`}
        data-testid="citation"
        x1={a.x}
        y1={a.y}
        x2={b.x}
        y2={b.y}
        style={paint(undefined, "var(--muted-foreground)")}
        strokeOpacity={0.45}
        strokeWidth={1}
        className={FADE}
        opacity={on ? 1 : DIM}
      />
    );
  });

  // --- idea arrows ---
  const arrows: ReactNode[] = [];
  const badges: ReactNode[] = [];
  for (const idea of ideas) {
    const A = nodeById.get(idea.source_doc);
    const targetNode = idea.target_doc === null ? null : nodeById.get(idea.target_doc);
    const B: { x: number; y: number } | null | undefined = idea.target_doc === null ? problemPos : targetNode;
    if (!A || !B) continue;
    const isSel = selIdea?.id === idea.id;
    const dx = B.x - A.x;
    const dy = B.y - A.y;
    const len = Math.hypot(dx, dy) || 1;
    const bend = Math.min(60, len * 0.2);
    const mx = (A.x + B.x) / 2 - (dy / len) * bend;
    const my = (A.y + B.y) / 2 + (dx / len) * bend;
    // Stop the arrow at the target's edge (circle, or the problem's rectangle).
    const tx = B.x - mx;
    const ty = B.y - my;
    const tl = Math.hypot(tx, ty) || 1;
    const ux = tx / tl;
    const uy = ty / tl;
    const rb = targetNode
      ? targetNode.r + (isSel ? 7 : 3)
      : Math.min(PROBLEM_HALF.w / Math.max(Math.abs(ux), 0.01), PROBLEM_HALF.h / Math.max(Math.abs(uy), 0.01)) + 4;
    const bx = B.x - ux * rb;
    const by = B.y - uy * rb;
    const d = `M${A.x},${A.y} Q${mx},${my} ${bx},${by}`;
    const opacity = leavingIds?.includes(idea.id) ? 0 : focus && !focus.ideas.has(idea.id) ? DIM : 1;
    const lineOpacity = STATUS_OPACITY[idea.status];
    const label = t("ideas:graph.ideaLabel", "{{title}}: {{source}} → {{target}} ({{status}})", {
      title: idea.title,
      source: docTitle(idea.source_doc),
      target: docTitle(idea.target_doc),
      status: statusLabel(idea.status),
    });
    const select = () => onSelect?.({ kind: "idea", id: idea.id });
    arrows.push(
      <g
        key={idea.id}
        data-testid={`idea-${idea.id}`}
        className={cn("group cursor-pointer", FOCUSABLE, FADE)}
        opacity={opacity}
        role="button"
        tabIndex={0}
        aria-label={label}
        aria-pressed={isSel}
        onClick={(e) => {
          e.stopPropagation();
          select();
        }}
        onKeyDown={(e) => activate(e, select)}
        onFocus={() => {
          if (!pressing.current) setKbIdea(idea.id);
        }}
        onBlur={() => setKbIdea((cur) => (cur === idea.id ? null : cur))}
      >
        <path
          data-testid={`idea-line-${idea.id}`}
          d={d}
          fill="none"
          style={paint(undefined, "var(--foreground)")}
          strokeOpacity={isSel ? 1 : lineOpacity}
          strokeWidth={isSel ? 2.6 : 1.5}
          strokeDasharray={isSel ? undefined : STATUS_DASH[idea.status]}
          strokeLinecap="round"
          // Keyboard focus: full opacity and bold, like a selection preview.
          className="group-focus-visible:[stroke-opacity:1] group-focus-visible:[stroke-width:2.6]"
          // A keyboard-focused line is drawn like a selection (full opacity, 2.6 wide), so it takes the same head.
          markerEnd={`url(#${isSel || kbIdea === idea.id ? `${uid}-arrow-sel` : markerId(uid, lineOpacity)})`}
        />
        {/* A wide transparent hit area along the whole curve. */}
        <path d={`M${A.x},${A.y} Q${mx},${my} ${B.x},${B.y}`} fill="none" stroke="transparent" strokeWidth={14}>
          <title>{label}</title>
        </path>
      </g>,
    );
    const Icon = STATUS_ICON[idea.status];
    if (Icon) {
      // Mid-point of the quadratic curve.
      const gx = (A.x + 2 * mx + bx) / 4;
      const gy = (A.y + 2 * my + by) / 4;
      badges.push(
        <g
          key={idea.id}
          data-testid={`idea-badge-${idea.id}`}
          data-status={idea.status}
          // A pointer target for the idea it sits on (the idea's own group is the keyboard button).
          data-graph-item=""
          transform={`translate(${gx - 9},${gy - 9})`}
          className={cn("cursor-pointer", FADE)}
          opacity={opacity}
          onClick={(e) => {
            e.stopPropagation();
            select();
          }}
        >
          <circle cx={9} cy={9} r={9} style={paint("var(--background)", "var(--border)")} strokeWidth={1} />
          <Icon
            x={4}
            y={4}
            width={10}
            height={10}
            style={{ color: "var(--foreground)" }}
            strokeWidth={2.4}
            aria-hidden="true"
          />
          <title>{statusLabel(idea.status)}</title>
        </g>,
      );
    }
  }

  // --- document nodes ---
  const ringCircle = (n: LayoutNode, extra?: { className?: string; testId?: string }) => (
    <circle
      data-testid={extra?.testId}
      cx={n.x}
      cy={n.y}
      r={n.r + 4}
      fill="none"
      style={paint(undefined, "var(--foreground)")}
      strokeWidth={2.5}
      className={extra?.className}
    />
  );
  const nodes = shown.nodes.map((n) => {
    const doc = docById.get(n.id);
    // A document removed mid-tween has no row any more; skip its last frames.
    if (!doc) return null;
    const doms = doc.domains.filter((d) => domainName.has(d));
    const role =
      selIdea?.source_doc === n.id
        ? t("ideas:graph.source", "Source")
        : selIdea?.target_doc === n.id
          ? t("ideas:graph.target", "Target")
          : null;
    const isSelDoc = selected?.kind === "doc" && selected.id === n.id;
    // The tooltip lists every domain; the fill shows only the first two.
    const names = doms.map((d) => domainName.get(d)).join(" + ");
    const label = [doc.title, names, doc.is_private ? t("ideas:graph.private", "Private document") : ""]
      .filter(Boolean)
      .join(" · ");
    const select = () => onSelect?.({ kind: "doc", id: n.id });
    return (
      <g
        key={n.id}
        data-testid={`node-${n.id}`}
        className={cn("group cursor-pointer", FOCUSABLE, FADE)}
        opacity={dimDoc(n.id)}
        role="button"
        tabIndex={0}
        aria-label={label}
        aria-pressed={isSelDoc}
        onClick={(e) => {
          e.stopPropagation();
          select();
        }}
        onKeyDown={(e) => activate(e, select)}
      >
        {(role || isSelDoc) && ringCircle(n)}
        {role && (
          <text
            x={n.x}
            y={n.y - n.r - 9}
            textAnchor="middle"
            fontSize={9.5}
            fontWeight={600}
            style={paint("var(--foreground)")}
          >
            {role}
          </text>
        )}
        {ringCircle(n, { className: FOCUS_RING, testId: "focus-ring" })}
        {/* Split fill uses the first two domains only (the tooltip names all of
            them): more slices would be unreadable at r = 8. */}
        {doms.length > 1 ? (
          <>
            <path
              data-testid="node-half"
              d={halfCircle(n.x, n.y, n.r, 0)}
              style={paint(colorOf(doms[0]), "var(--background)")}
              strokeWidth={2}
            />
            <path
              data-testid="node-half"
              d={halfCircle(n.x, n.y, n.r, 1)}
              style={paint(colorOf(doms[1]), "var(--background)")}
              strokeWidth={2}
            />
          </>
        ) : (
          <circle
            data-testid="node-body"
            cx={n.x}
            cy={n.y}
            r={n.r}
            style={paint(colorOf(doms[0]), "var(--background)")}
            strokeWidth={2}
          />
        )}
        {doc.is_private && (
          <Lock
            data-testid="node-lock"
            x={n.x + n.r - 4}
            y={n.y - n.r - 10}
            width={10}
            height={10}
            style={{ color: "var(--muted-foreground)" }}
            strokeWidth={2.4}
            aria-hidden="true"
          />
        )}
        {(() => {
          const place = labels.get(n.id) ?? { mode: "full" as const, side: "below" as const };
          const hidden = place.mode === "hidden";
          const above = place.side === "above" && !hidden;
          const text = truncateLabel(doc.title, place.mode === "short" ? SHORT_LABEL_CHARS : undefined);
          return (
            <text
              data-testid={`node-label-${n.id}`}
              data-label={place.mode}
              x={n.x}
              y={above ? n.y - n.r - 5 * labelScale : n.y + n.r + LABEL_DY * labelScale}
              textAnchor="middle"
              fontSize={11 * labelScale}
              style={paint("var(--foreground)", "var(--background)")}
              strokeWidth={3 * labelScale}
              paintOrder="stroke"
              className={cn(
                "pointer-events-none transition-opacity duration-150 motion-reduce:transition-none",
                hidden ? "opacity-0 group-hover:opacity-100 group-focus-visible:opacity-100" : "opacity-100",
              )}
            >
              {text}
            </text>
          );
        })()}
        <title>{label}</title>
      </g>
    );
  });

  // --- the problem node ---
  let problemNode: ReactNode = null;
  if (problemPos && graph.problem) {
    const { x, y } = problemPos;
    const isSel = selected?.kind === "problem";
    const isTarget = selIdea !== undefined && selIdea.target_doc === null;
    const select = () => onSelect?.({ kind: "problem" });
    const ringRect = (extra?: { className?: string; testId?: string }) => (
      <rect
        data-testid={extra?.testId}
        x={x - PROBLEM_HALF.w - 4}
        y={y - PROBLEM_HALF.h - 4}
        width={PROBLEM_HALF.w * 2 + 8}
        height={PROBLEM_HALF.h * 2 + 8}
        rx={11}
        fill="none"
        style={paint(undefined, "var(--foreground)")}
        strokeWidth={2.5}
        className={extra?.className}
      />
    );
    problemNode = (
      <g
        data-testid="problem-node"
        className={cn("group cursor-pointer", FOCUSABLE, FADE)}
        opacity={focus && !focus.problem ? DIM : 1}
        role="button"
        tabIndex={0}
        aria-label={`${t("ideas:graph.problem", "Your problem")}: ${graph.problem.text}`}
        aria-pressed={isSel}
        onClick={(e) => {
          e.stopPropagation();
          select();
        }}
        onKeyDown={(e) => activate(e, select)}
      >
        {(isSel || isTarget) && ringRect()}
        {ringRect({ className: FOCUS_RING, testId: "focus-ring" })}
        {isTarget && (
          <text
            x={x}
            y={y - PROBLEM_HALF.h - 9}
            textAnchor="middle"
            fontSize={9.5}
            fontWeight={600}
            style={paint("var(--foreground)")}
          >
            {t("ideas:graph.target", "Target")}
          </text>
        )}
        <rect
          data-testid="problem-body"
          x={x - PROBLEM_HALF.w}
          y={y - PROBLEM_HALF.h}
          width={PROBLEM_HALF.w * 2}
          height={PROBLEM_HALF.h * 2}
          rx={8}
          style={paint("var(--foreground)")}
        />
        <text x={x} y={y - 2} textAnchor="middle" fontSize={11} fontWeight={600} style={paint("var(--background)")}>
          {t("ideas:graph.problem", "Your problem")}
        </text>
        <text x={x} y={y + 11} textAnchor="middle" fontSize={9.5} style={paint("var(--background)")}>
          {truncateToWidth(graph.problem.text, PROBLEM_HALF.w * 2 - 14, 9.5)}
        </text>
        <title>{graph.problem.text}</title>
      </g>
    );
  }

  const marker = (id: string, size: number, opacity: number) => (
    <marker
      key={id}
      id={id}
      data-testid="arrow-marker"
      viewBox="0 0 10 10"
      refX={9}
      refY={5}
      markerWidth={size}
      markerHeight={size}
      orient="auto-start-reverse"
    >
      <path d="M0,0 L10,5 L0,10 Z" style={paint("var(--foreground)")} fillOpacity={opacity} />
    </marker>
  );

  return (
    <svg
      data-testid="idea-graph"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="xMidYMid meet"
      role="group"
      aria-label={t("ideas:graph.aria", "Idea graph")}
      ref={svgRef}
      // Focusable only by script: the drawer returns focus here when its item is gone.
      tabIndex={onBackgroundClick ? -1 : undefined}
      className={cn("block h-full w-full touch-none select-none outline-none", onBackgroundClick && "cursor-grab active:cursor-grabbing", className)}
      onClick={onBackgroundClick}
      onPointerDownCapture={() => {
        pressing.current = true;
      }}
      onPointerUpCapture={() => {
        pressing.current = false;
      }}
      onKeyDownCapture={() => {
        pressing.current = false;
      }}
    >
      <defs>
        {MARKER_OPACITIES.map((o) => marker(markerId(uid, o), 7, o))}
        {marker(`${uid}-arrow-sel`, 8, 1)}
      </defs>
      <g
        data-testid="graph-viewport"
        style={
          viewTransform
            ? { transform: `translate(${viewTransform.x}px, ${viewTransform.y}px) scale(${viewTransform.k})` }
            : undefined
        }
        className={cn(!viewLive && "transition-transform duration-300 ease-out motion-reduce:transition-none")}
      >
        <g>{hulls}</g>
        <g>{citations}</g>
        <g>{arrows}</g>
        {problemNode}
        <g>{nodes}</g>
        <g>{badges}</g>
      </g>
    </svg>
  );
}

export const IdeaGraph = memo(IdeaGraphImpl);
