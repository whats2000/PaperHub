import { useEffect, useRef, useState } from "react";
import {
  ArrowRight,
  Circle,
  CircleCheck,
  CircleX,
  EyeOff,
  FlaskConical,
  Star,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { useReasonText } from "@/lib/ideaActions";
import { domainColorVar } from "@/lib/domainColors";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { usePresence } from "@/hooks/usePresence";
import { matchesFilter, type IdeaFilter, type IdeationSelection } from "@/store/ideation";
import type { GraphResponse, Idea, IdeaStatus } from "@/types/ideation";

/** A card leaving a column fades out this long before it unmounts. */
const CARD_EXIT_MS = 150;

type ColumnKey = "new" | "saved" | "trying" | "done" | "dismissed";

const COLUMNS: { key: ColumnKey; statuses: IdeaStatus[]; icon: LucideIcon; fallback: string }[] = [
  { key: "new", statuses: ["new"], icon: Circle, fallback: "New" },
  { key: "saved", statuses: ["saved"], icon: Star, fallback: "Saved" },
  { key: "trying", statuses: ["trying"], icon: FlaskConical, fallback: "Trying" },
  { key: "done", statuses: ["worked", "failed"], icon: CircleCheck, fallback: "Has result" },
];
const DISMISSED_COLUMN = { key: "dismissed" as const, statuses: ["dismissed" as IdeaStatus], icon: EyeOff, fallback: "Dismissed" };

const STATUS_ICON: Record<IdeaStatus, LucideIcon> = {
  new: Circle,
  saved: Star,
  trying: FlaskConical,
  worked: CircleCheck,
  failed: CircleX,
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

/**
 * The items plus, for a moment, the ones that just left (at their old place),
 * so a card moving to another column or dismissed fades out instead of vanishing.
 */
function useWithExits(items: Idea[]): { idea: Idea; leaving: boolean }[] {
  const sig = items.map((i) => i.id).join(",");
  const [prev, setPrev] = useState({ sig, items });
  const [ghosts, setGhosts] = useState<{ idea: Idea; index: number }[]>([]);
  if (prev.sig !== sig) {
    const ids = new Set(items.map((i) => i.id));
    const gone = prev.items.flatMap((idea, index) => (ids.has(idea.id) ? [] : [{ idea, index }]));
    setPrev({ sig, items });
    setGhosts((g) => [...g.filter((x) => !ids.has(x.idea.id) && !gone.some((y) => y.idea.id === x.idea.id)), ...gone]);
  }
  useEffect(() => {
    if (ghosts.length === 0) return;
    const timer = setTimeout(() => setGhosts([]), prefersReducedMotion() ? 0 : CARD_EXIT_MS);
    return () => clearTimeout(timer);
  }, [ghosts]);
  const out = items.map((idea) => ({ idea, leaving: false }));
  for (const g of [...ghosts].sort((a, b) => a.index - b.index)) {
    out.splice(Math.min(g.index, out.length), 0, { idea: g.idea, leaving: true });
  }
  return out;
}

export interface IdeaKanbanProps {
  graph: GraphResponse;
  /** The scope's ideas; the board shows those under `filter` (dismissed only under its own). */
  ideas: Idea[];
  filter: IdeaFilter;
  selected: IdeationSelection;
  onSelect: (selection: IdeationSelection) => void;
  /** Room on the right for the open drawer, so no card is stuck under it. */
  rightInset?: number;
}

function Column({
  label,
  icon: Icon,
  ideas,
  card,
  empty,
}: {
  label: string;
  icon: LucideIcon;
  ideas: Idea[];
  card: (idea: Idea, leaving: boolean) => React.ReactNode;
  empty: string;
}) {
  const rows = useWithExits(ideas);
  const placeholder = usePresence(rows.length === 0);
  return (
    <section aria-label={label} className="flex min-w-[168px] flex-1 flex-col gap-2 rounded-lg bg-muted/40 p-2.5">
      <h3 className="flex items-center gap-1.5 px-0.5 text-[12.5px] font-semibold">
        <Icon className="size-3.5 text-muted-foreground" aria-hidden="true" />
        {label}
        <span className="ml-auto text-[11px] font-normal text-muted-foreground tabular-nums">{ideas.length}</span>
      </h3>
      {rows.map(({ idea, leaving }) => card(idea, leaving))}
      {placeholder.mounted && (
        <p
          data-state={placeholder.state}
          className="px-0.5 py-2 text-xs text-muted-foreground duration-150 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 fill-mode-forwards motion-reduce:animate-none"
        >
          {empty}
        </p>
      )}
    </section>
  );
}

/** The cards view: ideas of the current scope in status columns. */
export function IdeaKanban({ graph, ideas, filter, selected, onSelect, rightInset = 0 }: IdeaKanbanProps) {
  const { t } = useTranslation("ideas");
  const reasonText = useReasonText();
  const docs = new Map(graph.documents.map((d) => [d.id, d]));
  const domains = new Map(graph.domains.map((d) => [d.id, d]));
  const docColor = (id: number | null) => {
    if (id === null) return "var(--foreground)";
    const first = docs.get(id)?.domains[0];
    return domainColorVar(first === undefined ? null : (domains.get(first)?.color_index ?? null));
  };
  // Opening the drawer reflows the board into the room left of it (the padding below);
  // when the columns still don't fit, scroll the selected card out from under the drawer.
  const board = useRef<HTMLDivElement>(null);
  const selectedIdea = selected?.kind === "idea" ? selected.id : null;
  useEffect(() => {
    if (selectedIdea === null || rightInset === 0) return;
    const card = board.current?.querySelector<HTMLElement>(`[data-testid="idea-card-${selectedIdea}"]:not([data-leaving])`);
    card?.scrollIntoView?.({ inline: "nearest", block: "nearest", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  }, [selectedIdea, rightInset]);
  const columns = filter === "dismissed" ? [DISMISSED_COLUMN] : COLUMNS;
  const shown = ideas.filter((i) => matchesFilter(i.status, filter));

  const card = (idea: Idea, leaving: boolean) => {
    const Icon = STATUS_ICON[idea.status];
    const on = selected?.kind === "idea" && selected.id === idea.id;
    const chats = idea.session_ids.length;
    return (
      <button
        key={idea.id}
        type="button"
        data-testid={`idea-card-${idea.id}`}
        data-leaving={leaving || undefined}
        aria-pressed={on}
        tabIndex={leaving ? -1 : undefined}
        onClick={() => onSelect({ kind: "idea", id: idea.id })}
        className={cn(
          "w-full rounded-md border bg-card p-2.5 text-left text-card-foreground shadow-xs transition-colors hover:border-foreground/40 focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none",
          "animate-in fade-in-0 zoom-in-95 duration-150 motion-reduce:animate-none",
          on && "border-foreground ring-1 ring-foreground",
          leaving && "pointer-events-none animate-out fade-out-0 zoom-out-95 fill-mode-forwards",
        )}
      >
        <h4 className="mb-1.5 text-[13px] leading-snug font-semibold">{idea.title}</h4>
        <div className="mb-1.5 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full" style={{ background: docColor(idea.source_doc) }} />
          <span className="truncate">{docs.get(idea.source_doc)?.title ?? ""}</span>
          <ArrowRight className="size-3 shrink-0" aria-label={t("kanban.to", "to")} />
          <span aria-hidden="true" className="size-2 shrink-0 rounded-full" style={{ background: docColor(idea.target_doc) }} />
          <span className="truncate">
            {idea.target_doc === null ? t("graph.problem", "Your problem") : (docs.get(idea.target_doc)?.title ?? "")}
          </span>
        </div>
        <div className="flex flex-wrap items-center gap-x-1.5 text-xs text-muted-foreground">
          <Icon className="size-3.5" aria-hidden="true" />
          <span>
            {t(`graph.status.${idea.status}`, STATUS_FALLBACK[idea.status])}
            {idea.status === "dismissed" && idea.dismiss_reasons.length > 0 && `: ${reasonText(idea.dismiss_reasons)}`}
          </span>
          {chats > 0 && <span>· {t("kanban.chats", { count: chats, defaultValue: "{{count}} chats" })}</span>}
        </div>
        {idea.note && (
          <p className="mt-1.5 line-clamp-3 border-l-2 pl-2 text-xs whitespace-pre-wrap text-muted-foreground">{idea.note}</p>
        )}
      </button>
    );
  };

  return (
    <div
      ref={board}
      data-testid="idea-kanban"
      // Focus target when the drawer closes and the selection has no card.
      tabIndex={-1}
      className="flex min-h-0 flex-1 flex-col overflow-auto px-5 pt-1 pb-4 outline-none transition-[padding] duration-200 ease-out motion-reduce:transition-none"
      style={{ paddingRight: 20 + rightInset }}
    >
      <p className="mb-2 text-sm text-muted-foreground">
        {graph.problem
          ? t("kanban.problem", "For your problem: {{text}}", { text: graph.problem.text })
          : t("kanban.explore", "Free exploration: cross-domain pairs within your library")}
      </p>
      <div className="flex items-start gap-3">
        {columns.map((c) => (
          <Column
            key={c.key}
            label={t(`kanban.columns.${c.key}`, c.fallback)}
            icon={c.icon}
            ideas={shown.filter((i) => c.statuses.includes(i.status))}
            card={card}
            empty={t("kanban.empty", "No ideas")}
          />
        ))}
      </div>
    </div>
  );
}
