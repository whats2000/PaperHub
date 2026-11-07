import { useId, useRef, useState, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { AlertCircle, Check, ChevronDown, Loader2, Network, Plus, Target } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { useIdeationStore, type IdeationScope } from "@/store/ideation";
import type { Problem } from "@/types/ideation";

function formatDate(iso: string, lang: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(lang, { month: "short", day: "numeric" });
}

interface RowProps {
  selected: boolean;
  icon: ReactNode;
  title: string;
  sub: string;
  onClick: () => void;
}

function Row({ selected, icon, title, sub, onClick }: RowProps) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={selected}
      // Roving focus: only the selected option is in the tab order.
      tabIndex={selected ? 0 : -1}
      onClick={onClick}
      className={cn(
        "flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none",
        selected && "bg-accent",
      )}
    >
      <span className="mt-0.5 shrink-0 text-muted-foreground">{icon}</span>
      <span className="min-w-0 flex-1">
        <span className="block truncate">{title}</span>
        <span className="block text-xs text-muted-foreground">{sub}</span>
      </span>
      {selected && <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />}
    </button>
  );
}

/** Picks the scope: free exploration, one of the user's problems, or a new one. */
export function ProblemSwitcher({ entryRef }: { entryRef: RefObject<HTMLTextAreaElement | null> }) {
  const { t, i18n } = useTranslation("ideas");
  const [open, setOpen] = useState(false);
  // "Describe a new problem" hands focus to the entry box instead of the trigger.
  const toEntry = useRef(false);
  const groupId = useId();
  const problems = useIdeationStore((s) => s.problems);
  const scope = useIdeationStore((s) => s.scope);
  const exploreCount = useIdeationStore((s) => s.exploreIdeaCount);
  const setScope = useIdeationStore((s) => s.setScope);
  const listError = useIdeationStore((s) => s.listError);
  const loadProblems = useIdeationStore((s) => s.loadProblems);

  // Arrow keys move focus between the options (listbox pattern).
  const onListKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const opts = Array.from(e.currentTarget.querySelectorAll<HTMLElement>('[role="option"]'));
    if (opts.length === 0) return;
    const i = opts.indexOf(document.activeElement as HTMLElement);
    let next: number | null = null;
    if (e.key === "ArrowDown") next = i < 0 ? 0 : (i + 1) % opts.length;
    else if (e.key === "ArrowUp")
      next = i < 0 ? opts.length - 1 : (i - 1 + opts.length) % opts.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = opts.length - 1;
    if (next === null) return;
    e.preventDefault();
    opts[next]?.focus();
  };

  const current = scope === "explore" ? null : problems.find((p) => p.id === scope);
  const label = current ? current.text : t("switcher.explore", "Free exploration");

  const pick = (next: IdeationScope) => {
    toEntry.current = false;
    setOpen(false);
    setScope(next);
  };

  const problemSub = (p: Problem) => {
    if (p.status === "generating") return t("switcher.generating", "Generating…");
    const count = t("switcher.ideaCount", "{{count}} ideas", { count: p.idea_count });
    return `${formatDate(p.created_at, i18n.language)} · ${count}`;
  };

  const exploreDesc = t("switcher.exploreSub", "Cross-domain pairs within your library");
  const exploreSub =
    exploreCount === null
      ? exploreDesc
      : `${exploreDesc} · ${t("switcher.ideaCount", "{{count}} ideas", { count: exploreCount })}`;

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (next) toEntry.current = false;
        setOpen(next);
      }}
    >
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            size="lg"
            className="max-w-[300px] shrink-0"
            aria-label={t("switcher.aria", "Problem: {{label}}", { label })}
          />
        }
      >
        {scope === "explore" ? <Network /> : <Target />}
        <span className="truncate">{label}</span>
        {listError && (
          <AlertCircle
            className="text-muted-foreground"
            aria-label={t("switcher.listFailed", "Couldn't load problems")}
          />
        )}
        {current?.status === "generating" ? (
          <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
        ) : (
          <ChevronDown className="text-muted-foreground" aria-hidden="true" />
        )}
      </PopoverTrigger>
      <PopoverContent
        side="bottom"
        align="start"
        className="w-[360px] origin-(--transform-origin) p-1 duration-150 motion-reduce:animate-none"
        finalFocus={() => (toEntry.current ? entryRef.current : true)}
      >
        <div role="listbox" aria-label={t("switcher.menu", "Problems")} onKeyDown={onListKeyDown}>
          <Row
            selected={scope === "explore"}
            icon={<Network className="h-3.5 w-3.5" />}
            title={t("switcher.explore", "Free exploration")}
            sub={exploreSub}
            onClick={() => pick("explore")}
          />
          {problems.length > 0 && (
            <div role="group" aria-labelledby={groupId}>
              <div role="presentation" className="-mx-1 my-1 border-t border-border" />
              <div
                id={groupId}
                role="presentation"
                className="px-2 pt-1 pb-0.5 text-xs text-muted-foreground"
              >
                {t("switcher.yourProblems", "Your problems")}
              </div>
              {problems.map((p) => (
                <Row
                  key={p.id}
                  selected={scope === p.id}
                  icon={<Target className="h-3.5 w-3.5" />}
                  title={p.text}
                  sub={problemSub(p)}
                  onClick={() => pick(p.id)}
                />
              ))}
            </div>
          )}
        </div>
        {listError && (
          <div
            role="alert"
            className="flex items-center gap-2 px-2 py-1.5 text-xs text-muted-foreground"
          >
            <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            <span className="flex-1">{t("switcher.listFailed", "Couldn't load problems")}</span>
            <Button variant="outline" size="xs" onClick={() => void loadProblems()}>
              {t("switcher.retry", "Retry")}
            </Button>
          </div>
        )}
        <div className="-mx-1 my-1 border-t border-border" />
        <button
          type="button"
          onClick={() => {
            toEntry.current = true;
            setOpen(false);
          }}
          className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none"
        >
          <Plus className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true" />
          {t("switcher.new", "Describe a new problem")}
        </button>
      </PopoverContent>
    </Popover>
  );
}
