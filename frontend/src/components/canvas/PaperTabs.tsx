/** The paper tabs the reader and the notes pane share, so both draw the
 * same set the same way: a solid tab for each of the chat's papers (three,
 * then "…"), then a dashed tab for a paper not in the chat (Add brings it
 * in, × closes it), its title italic while it is temporary (opened by a
 * link, replaced by the next one; a double click keeps it). The parent
 * owns the `tablist` (with `roveTabs` from `@/lib/paperTabs` for the arrow
 * keys). */
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { FileText, Plus, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";

import "./paperTabs.css";

/** Solid tabs shown before the rest go into the "…" menu. */
const MAX_VISIBLE_PAPER_TABS = 3;
/** How long a closed tab takes to shrink away. */
const LEAVE_MS = 200;

export interface PaperTabItem {
  id: number;
  title: string;
}

export interface OutsidePaperTabItem extends PaperTabItem {
  /** Temporary: replaced by the next paper opened by looking. */
  preview: boolean;
}

interface Props {
  /** The chat's papers, in order. */
  papers: PaperTabItem[];
  /** Papers shown but not in the chat (dashed). */
  outside: OutsidePaperTabItem[];
  /** The paper shown, or null (no paper tab selected). */
  selected: number | null;
  onSelect: (paperId: number) => void;
  onAdd: (paperId: number) => void;
  onClose: (paperId: number) => void;
  onKeep: (paperId: number) => void;
  /** A solid tab's tooltip (default: the paper's title); a dashed tab's
   *  says it is not in the chat. */
  tabTitle?: (paper: PaperTabItem) => string;
  /** The tab's element id, and the panel it shows, for the pane's tabpanel. */
  tabId?: (paperId: number) => string;
  panelId?: string;
  /** A new value (another chat) redraws the tabs without motion. */
  resetKey?: unknown;
  /** False when another tab of the strip is selected (the notes pane's
   *  This chat): none of these is then the strip's Tab stop. */
  tabStop?: boolean;
}

type Tab = (PaperTabItem & { dashed: false }) | (OutsidePaperTabItem & { dashed: true });

const tabSig = (t: Tab) => `${t.id}:${t.dashed ? (t.preview ? "p" : "k") : "s"}:${t.title}`;

interface Shown {
  tab: Tab;
  leaving: boolean;
}

/** The tabs drawn: the current ones, plus those just gone, kept at their
 * place while they shrink away; `entered`: tabs that just opened. */
function usePresence(tabs: Tab[], resetKey: unknown) {
  const sig = tabs.map(tabSig).join("|");
  const [state, setState] = useState(() => ({
    sig,
    resetKey,
    shown: tabs.map((tab): Shown => ({ tab, leaving: false })),
    entered: new Set<number>(),
  }));
  // (Adjusted while rendering: the tabs changed.)
  if (state.sig !== sig || state.resetKey !== resetKey) {
    const fresh = state.resetKey !== resetKey || prefersReducedMotion();
    const shown = tabs.map((tab): Shown => ({ tab, leaving: false }));
    const entered = new Set<number>();
    if (!fresh) {
      const ids = new Set(tabs.map((t) => t.id));
      state.shown.forEach((s, i) => {
        if (!ids.has(s.tab.id)) shown.splice(Math.min(i, shown.length), 0, { tab: s.tab, leaving: true });
      });
      const before = new Set(state.shown.filter((s) => !s.leaving).map((s) => s.tab.id));
      for (const t of tabs) if (!before.has(t.id)) entered.add(t.id);
      for (const id of state.entered) if (ids.has(id)) entered.add(id);
    }
    setState({ sig, resetKey, shown, entered });
  }
  const leaving = state.shown.some((s) => s.leaving);
  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(
      () => setState((s) => ({ ...s, shown: s.shown.filter((x) => !x.leaving) })),
      LEAVE_MS,
    );
    return () => clearTimeout(timer);
  }, [leaving, state.sig]);
  const settle = (id: number) =>
    setState((s) => {
      if (!s.entered.has(id)) return s;
      const entered = new Set(s.entered);
      entered.delete(id);
      return { ...s, entered };
    });
  // Current tabs draw from the props (fresh titles); leaving ones as they were.
  const byId = new Map(tabs.map((t) => [t.id, t]));
  return {
    shown: state.shown.map((s) => ({ tab: s.leaving ? s.tab : (byId.get(s.tab.id) ?? s.tab), leaving: s.leaving })),
    entered: state.entered,
    settle,
  };
}

export function PaperTabs({
  papers,
  outside,
  selected,
  onSelect,
  onAdd,
  onClose,
  onKeep,
  tabTitle,
  tabId,
  panelId,
  resetKey,
  tabStop = true,
}: Props) {
  const { t } = useTranslation(["canvas", "paperTabs"]);
  const [moreOpen, setMoreOpen] = useState(false);
  const visible = papers.slice(0, MAX_VISIBLE_PAPER_TABS);
  const hidden = papers.slice(MAX_VISIBLE_PAPER_TABS);
  const inChat = new Set(papers.map((p) => p.id));
  const tabs: Tab[] = [
    ...visible.map((p): Tab => ({ ...p, dashed: false })),
    ...outside.filter((o) => !inChat.has(o.id)).map((o): Tab => ({ ...o, dashed: true })),
  ];
  const { shown, entered, settle } = usePresence(tabs, resetKey);
  const moreSelected = hidden.some((p) => p.id === selected);
  // Reachable by Tab: the selected tab, else the first.
  const focusable = !tabStop ? null : tabs.some((x) => x.id === selected) ? selected : (tabs[0]?.id ?? null);

  // The selected tab is scrolled into the strip's view (a narrow pane).
  const root = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const strip = root.current?.parentElement;
    const el = strip?.querySelector<HTMLElement>('[data-paper-tab][data-selected="true"], [data-paper-more][data-selected="true"]');
    if (!strip || !el) return;
    const left = el.getBoundingClientRect().left - strip.getBoundingClientRect().left + strip.scrollLeft;
    if (left < strip.scrollLeft) strip.scrollLeft = Math.max(0, left - 4);
    else if (left + el.offsetWidth > strip.scrollLeft + strip.clientWidth)
      strip.scrollLeft = left + el.offsetWidth - strip.clientWidth + 4;
  }, [selected, papers.length, outside.length]);

  const titleOf = (tab: Tab) => {
    if (!tab.dashed) return tabTitle ? tabTitle(tab) : tab.title;
    const base = t("canvas:panel.viewingTitle", { title: tab.title });
    return tab.preview ? `${base} ${t("paperTabs:previewHint", "(opened by a link; double-click to keep)")}` : base;
  };

  const more = hidden.length > 0 && (
    <Popover open={moreOpen} onOpenChange={setMoreOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            data-paper-more
            data-selected={moreSelected ? "true" : "false"}
            className={cn(
              "h-6 shrink-0 rounded px-2 py-1 text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
              moreSelected
                ? "bg-foreground/10 text-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground",
            )}
            aria-label={t("canvas:panel.morePapers")}
            title={t("canvas:panel.morePapers")}
          />
        }
      >
        …
      </PopoverTrigger>
      <PopoverContent side="bottom" align="start" className="w-56 p-1">
        <div role="menu" aria-label={t("canvas:panel.morePapers")} className="flex flex-col gap-0.5">
          {hidden.map((p) => (
            <button
              key={p.id}
              type="button"
              role="menuitemradio"
              aria-checked={p.id === selected}
              onClick={() => {
                setMoreOpen(false);
                onSelect(p.id);
              }}
              className={cn(
                "w-full truncate rounded px-2 py-1.5 text-left text-xs font-medium transition-colors",
                p.id === selected
                  ? "bg-foreground/10 text-foreground"
                  : "text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
              title={p.title}
            >
              {p.title}
            </button>
          ))}
        </div>
      </PopoverContent>
    </Popover>
  );

  const lastVisible = visible[visible.length - 1]?.id;
  return (
    <>
      <span ref={root} hidden />
      {shown.map(({ tab, leaving }) => (
        <PaperTabShell
          key={tab.id}
          tab={tab}
          leaving={leaving}
          entering={entered.has(tab.id)}
          onEntered={() => settle(tab.id)}
          selected={!leaving && tab.id === selected}
          focusable={!leaving && tab.id === focusable}
          title={titleOf(tab)}
          tabId={tabId?.(tab.id)}
          panelId={panelId}
          onSelect={onSelect}
          onAdd={onAdd}
          onClose={onClose}
          onKeep={onKeep}
          after={!leaving && !tab.dashed && tab.id === lastVisible ? more : null}
        />
      ))}
    </>
  );
}

interface ShellProps {
  tab: Tab;
  leaving: boolean;
  entering: boolean;
  onEntered: () => void;
  selected: boolean;
  focusable: boolean;
  title: string;
  tabId?: string;
  panelId?: string;
  onSelect: (id: number) => void;
  onAdd: (id: number) => void;
  onClose: (id: number) => void;
  onKeep: (id: number) => void;
  after: ReactNode;
}

function PaperTabShell({
  tab,
  leaving,
  entering,
  onEntered,
  selected,
  focusable,
  title,
  tabId,
  panelId,
  onSelect,
  onAdd,
  onClose,
  onKeep,
  after,
}: ShellProps) {
  const { t } = useTranslation(["canvas", "paperTabs"]);
  const el = useRef<HTMLDivElement>(null);
  // Leaving: shrink from the width it has now (not from its max width).
  useLayoutEffect(() => {
    const node = el.current;
    if (!leaving || !node) return;
    node.style.maxWidth = `${node.getBoundingClientRect().width}px`;
    void node.offsetWidth;
    node.classList.add("paper-tab-leave");
  }, [leaving]);
  const preview = tab.dashed && tab.preview;
  return (
    <>
      <div
        ref={el}
        data-paper-tab
        data-paper-id={tab.id}
        data-dashed={tab.dashed ? "true" : "false"}
        data-preview={preview ? "true" : "false"}
        data-selected={selected ? "true" : "false"}
        data-leaving={leaving ? "" : undefined}
        aria-hidden={leaving || undefined}
        inert={leaving ? true : undefined}
        onAnimationEnd={entering ? onEntered : undefined}
        onDoubleClick={(e) => {
          if (preview && !(e.target as HTMLElement).closest("[data-tab-action]")) onKeep(tab.id);
        }}
        className={cn(
          "group/ptab inline-flex h-6 min-w-16 max-w-60 items-center overflow-hidden rounded text-xs font-medium transition-colors duration-150 motion-reduce:transition-none",
          entering && "paper-tab-enter",
          tab.dashed ? "shrink-0 border border-dashed border-border" : selected ? "shrink-0" : "shrink",
          selected
            ? "bg-foreground/10 text-foreground"
            : tab.dashed
              ? "bg-muted/40 text-muted-foreground hover:bg-muted hover:text-foreground"
              : "text-muted-foreground hover:bg-muted hover:text-foreground",
        )}
      >
        <button
          type="button"
          role="tab"
          id={tabId}
          aria-controls={panelId}
          aria-selected={selected}
          tabIndex={focusable ? 0 : -1}
          onClick={() => onSelect(tab.id)}
          title={title}
          className={cn(
            "inline-flex h-full min-w-0 items-center gap-1 rounded py-1 outline-none focus-visible:ring-2 focus-visible:ring-ring",
            tab.dashed ? "pr-1 pl-2" : "px-2",
          )}
        >
          <FileText className="h-3 w-3 shrink-0" aria-hidden />
          <span className={cn("min-w-0 truncate", tab.dashed && "max-w-[9rem]", preview && "italic")}>
            {tab.title}
          </span>
        </button>
        {tab.dashed && (
          <button
            type="button"
            data-tab-action
            tabIndex={focusable ? 0 : -1}
            onClick={() => onAdd(tab.id)}
            className="flex shrink-0 items-center gap-0.5 rounded px-1.5 py-1 font-medium text-primary hover:bg-primary/10"
            title={t("canvas:panel.addToReferences")}
          >
            <Plus className="h-3 w-3" aria-hidden />
            {t("canvas:panel.add")}
          </button>
        )}
        {tab.dashed && (
          <button
            type="button"
            data-tab-action
            tabIndex={focusable ? 0 : -1}
            onClick={() => onClose(tab.id)}
            aria-label={t("paperTabs:close", "Close {{title}}", { title: tab.title })}
            title={t("paperTabs:close", "Close {{title}}", { title: tab.title })}
            className={cn(
              "mr-1 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-[3px] text-muted-foreground transition-opacity duration-150 hover:bg-foreground/10 hover:text-foreground focus-visible:opacity-100 motion-reduce:transition-none",
              selected ? "opacity-100" : "opacity-0 group-hover/ptab:opacity-100",
            )}
          >
            <X className="h-3 w-3" aria-hidden />
          </button>
        )}
      </div>
      {after}
    </>
  );
}
