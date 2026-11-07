import { useState, type ReactNode } from "react";
import { Check, ChevronDown, EyeOff, FlaskConical, Lock, Star, X, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Collapsible } from "@/components/ui/collapsible";
import { domainColorVar } from "@/lib/domainColors";
import { cn } from "@/lib/utils";
import type { GraphDomain, IdeaStatus } from "@/types/ideation";

function Item({ swatch, children }: { swatch: ReactNode; children: ReactNode }) {
  return (
    <li className="flex items-center gap-[7px] leading-[1.9] whitespace-nowrap">
      <span aria-hidden="true" className="inline-flex w-[22px] shrink-0 justify-center">
        {swatch}
      </span>
      <span className="min-w-0 truncate">{children}</span>
    </li>
  );
}

function Group({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  return (
    <div className="mt-1.5 first:mt-0">
      <div className="mb-[3px] text-[11px] text-muted-foreground">{title}</div>
      <ul className={className}>{children}</ul>
    </div>
  );
}

const Dot = ({ color }: { color: string }) => <span className="size-[9px] rounded-full" style={{ background: color }} />;

const Line = ({ dash }: { dash?: string }) => (
  <svg width="22" height="6">
    <line x1="0" y1="3" x2="22" y2="3" stroke="currentColor" strokeWidth={1.6} strokeDasharray={dash} strokeLinecap={dash ? "round" : undefined} />
  </svg>
);

const STATUS_ICONS: [IdeaStatus, LucideIcon, string][] = [
  ["saved", Star, "Saved"],
  ["trying", FlaskConical, "Trying"],
  ["worked", Check, "Worked"],
  ["failed", X, "Failed"],
  ["dismissed", EyeOff, "Dismissed"],
];

/**
 * The graph legend: a vertical card pinned bottom-left, grouped by encoding.
 * Semi-transparent while idle, opaque on hover or focus; it never moves when
 * the drawer opens.
 */
export function GraphLegend({ domains, drawerOpen = false }: { domains: GraphDomain[]; drawerOpen?: boolean }) {
  const { t } = useTranslation("ideas");
  const [open, setOpen] = useState(true);
  // The "Other" row lists the domains it folds together while hovered, focused or clicked.
  const [otherOpen, setOtherOpen] = useState(false);
  // An open drawer folds the legend to its title so it never covers the nodes
  // the drawer is about; the reader can still open it again.
  const [seenDrawer, setSeenDrawer] = useState(drawerOpen);
  if (seenDrawer !== drawerOpen) {
    setSeenDrawer(drawerOpen);
    setOpen(!drawerOpen);
  }
  const colored = domains.filter((d) => d.color_index !== null).sort((a, b) => (a.color_index ?? 0) - (b.color_index ?? 0));
  const others = domains.filter((d) => d.color_index === null);
  const hasOther = others.length > 0;
  const title = t("ideas:legend.title", "Legend");

  return (
    <div
      data-testid="graph-legend"
      className={cn(
        "absolute bottom-3.5 left-3.5 z-[5] w-[206px] rounded-md border bg-popover text-xs text-popover-foreground shadow-md",
        "opacity-55 transition-opacity duration-200 hover:opacity-100 focus-within:opacity-100 motion-reduce:transition-none",
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between rounded-md py-1.5 pr-1.5 pl-2.5 text-xs font-semibold outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {title}
        <ChevronDown
          aria-hidden="true"
          className={cn("size-3.5 transition-transform duration-200 ease-out motion-reduce:transition-none", !open && "-rotate-90")}
        />
      </button>
      <Collapsible open={open}>
        <div className="max-h-[50vh] overflow-y-auto px-2.5 pb-2">
          <Group title={t("ideas:legend.domains", "Domain (colour)")}>
            {colored.map((d) => (
              <Item key={d.id} swatch={<Dot color={domainColorVar(d.color_index)} />}>
                {d.name}
              </Item>
            ))}
            {hasOther && (
              <li
                data-testid="legend-other"
                onMouseEnter={() => setOtherOpen(true)}
                onMouseLeave={() => setOtherOpen(false)}
                onFocus={() => setOtherOpen(true)}
                onBlur={() => setOtherOpen(false)}
              >
                <button
                  type="button"
                  aria-expanded={otherOpen}
                  onClick={() => setOtherOpen(true)}
                  title={t("ideas:legend.otherHint", "Domains beyond the first eight share this colour")}
                  className="flex w-full items-center gap-[7px] rounded-sm text-left leading-[1.9] whitespace-nowrap outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span aria-hidden="true" className="inline-flex w-[22px] shrink-0 justify-center">
                    <Dot color={domainColorVar(null)} />
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {t("ideas:legend.other", "Other")}
                    <span className="ml-1 text-muted-foreground tabular-nums">{others.length}</span>
                  </span>
                  <ChevronDown
                    aria-hidden="true"
                    className={cn("size-3 shrink-0 transition-transform duration-200 ease-out motion-reduce:transition-none", !otherOpen && "-rotate-90")}
                  />
                </button>
                <Collapsible open={otherOpen}>
                  <ul data-testid="legend-other-list" className="pb-1 pl-[29px] text-muted-foreground">
                    {others.map((d) => (
                      <li key={d.id} className="truncate leading-[1.7]" title={d.name}>
                        {d.name}
                      </li>
                    ))}
                  </ul>
                </Collapsible>
              </li>
            )}
          </Group>
          <Group title={t("ideas:legend.relation", "Relation")}>
            <Item
              swatch={
                <svg width="22" height="6">
                  <line x1="0" y1="3" x2="22" y2="3" style={{ stroke: "var(--muted-foreground)" }} strokeWidth={1} />
                </svg>
              }
            >
              {t("ideas:legend.citation", "Citation")}
            </Item>
            <Item
              swatch={
                <svg width="22" height="8">
                  <line x1="0" y1="4" x2="16" y2="4" stroke="currentColor" strokeWidth={1.5} strokeDasharray="4 3" />
                  <path d="M15 0 L22 4 L15 8 Z" fill="currentColor" />
                </svg>
              }
            >
              {t("ideas:legend.idea", "Idea: source → target")}
            </Item>
          </Group>
          <Group title={t("ideas:legend.status", "Idea status")}>
            <Item swatch={<Line dash="5 4" />}>{t("ideas:graph.status.new", "New")}</Item>
            <Item swatch={<Line />}>{t("ideas:legend.kept", "Saved / trying / worked")}</Item>
            <Item swatch={<Line dash="1.5 4" />}>{t("ideas:legend.ended", "Failed / dismissed")}</Item>
          </Group>
          <ul className="grid grid-cols-2">
            {STATUS_ICONS.map(([s, Icon, fallback]) => (
              <Item key={s} swatch={<Icon className="size-3.5" />}>
                {t(`ideas:graph.status.${s}`, fallback)}
              </Item>
            ))}
          </ul>
          <Group title={t("ideas:legend.document", "Document")}>
            <Item
              swatch={
                <svg width="22" height="14">
                  <circle cx="5" cy="7" r="4" style={{ fill: "var(--muted-foreground)" }} />
                  <circle cx="16" cy="7" r="6" style={{ fill: "var(--muted-foreground)" }} />
                </svg>
              }
            >
              {t("ideas:legend.size", "Size = related ideas")}
            </Item>
            <Item swatch={<Lock className="size-3.5" />}>{t("ideas:graph.private", "Private document")}</Item>
          </Group>
        </div>
      </Collapsible>
    </div>
  );
}
