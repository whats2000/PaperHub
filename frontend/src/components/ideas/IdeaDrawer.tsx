import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Lightbulb, Lock, X } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import type { DrawingPoint } from "@/hooks/usePanZoom";
import { usePresence } from "@/hooks/usePresence";
import { domainColorVar } from "@/lib/domainColors";
import { sectionLabel } from "@/lib/ideaActions";
import { DRAWER_WIDTH } from "@/lib/ideaGraphLayout";
import { cn } from "@/lib/utils";
import type { IdeationSelection } from "@/store/ideation";
import type { GraphDocument, GraphDomain, GraphResponse, Idea, IdeaStatus, Problem } from "@/types/ideation";

/** Matches the drawer's slide-out; the element unmounts when it has played. */
const EXIT_MS = 200;

type Selected = NonNullable<IdeationSelection>;

/** Extra content per selection kind, rendered under the read-only summary (Task 11's editors). */
export interface DrawerSlots {
  idea?: (idea: Idea) => ReactNode;
  doc?: (doc: GraphDocument) => ReactNode;
  domain?: (domain: GraphDomain) => ReactNode;
  problem?: (problem: Problem) => ReactNode;
}

export interface IdeaDrawerProps {
  graph: GraphResponse | null;
  /** The ideas currently shown (filtered), for the related-idea lists. */
  ideas: Idea[];
  selected: IdeationSelection;
  /** Select another item from inside the drawer, or `null` to close it. */
  onSelect: (selection: IdeationSelection) => void;
  /** Auto-pan the graph so the selection stays clear of the drawer. */
  panToKeepVisible?: (points: readonly DrawingPoint[], rightInset: number) => void;
  /** Drawing points to keep visible for a selection (endpoints and labels). */
  anchorsFor?: (selection: Selected) => DrawingPoint[];
  slots?: DrawerSlots;
  /** The graph element that stands for a selection, to get focus back when the drawer closes. */
  returnFocusTo?: (selection: Selected) => HTMLElement | SVGElement | null;
}

/** An open Base UI popup above the drawer: Escape belongs to it, not to us. */
const OPEN_POPUP =
  '[data-open][role="dialog"], [data-open][role="alertdialog"], [data-open][role="menu"], [data-open][role="listbox"]';

/** Escape typed into a field edits (or cancels) that field; it never closes the drawer. */
function isEditable(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]') !== null;
}

const STATUS_FALLBACK: Record<IdeaStatus, string> = {
  new: "New",
  saved: "Saved",
  trying: "Trying",
  worked: "Worked",
  failed: "Failed",
  dismissed: "Dismissed",
};

const selectionKey = (s: IdeationSelection) => (s ? `${s.kind}:${"id" in s ? s.id : ""}` : "");

function Label({ children }: { children: ReactNode }) {
  return <div className="mt-3 mb-1.5 text-[11.5px] text-muted-foreground">{children}</div>;
}

function Dot({ color }: { color: string }) {
  return <span aria-hidden="true" className="mt-[5px] size-[9px] shrink-0 rounded-full" style={{ background: color }} />;
}

/** A bordered row: an endpoint, or a clickable entry in a list. */
function Row({
  color,
  icon,
  title,
  sub,
  onClick,
}: {
  color?: string;
  icon?: ReactNode;
  title: ReactNode;
  sub?: ReactNode;
  onClick?: () => void;
}) {
  const body = (
    <>
      {color !== undefined && <Dot color={color} />}
      {icon}
      <div className="min-w-0">
        <div className="break-words">{title}</div>
        {sub && <small className="block text-xs text-muted-foreground">{sub}</small>}
      </div>
    </>
  );
  const cls = "flex w-full items-start gap-2 rounded-md border px-2.5 py-2 text-left text-[13px]";
  return onClick ? (
    <button
      type="button"
      onClick={onClick}
      className={cn(cls, "mb-1.5 transition-colors hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none")}
    >
      {body}
    </button>
  ) : (
    <div className={cls}>{body}</div>
  );
}

/**
 * The ideas detail drawer: a fixed 340 px panel that slides in over the right
 * edge of the graph (never squeezing it) and slides back out on close.
 */
export function IdeaDrawer({
  graph,
  ideas,
  selected,
  onSelect,
  panToKeepVisible,
  anchorsFor,
  slots,
  returnFocusTo,
}: IdeaDrawerProps) {
  const { t } = useTranslation("ideas");
  const open = selected !== null;
  const { mounted, state } = usePresence(open, EXIT_MS);
  // Keep showing the last selection while sliding out.
  const [shown, setShown] = useState<IdeationSelection>(selected);
  if (selected !== null && selected !== shown) setShown(selected);
  // The panel mounts off-screen, then slides in on the next frame.
  const [entered, setEntered] = useState(false);
  if (!mounted && entered) setEntered(false);
  useEffect(() => {
    if (!mounted || entered) return;
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, [mounted, entered]);

  const asideRef = useRef<HTMLElement>(null);
  // Close from inside the drawer: focus would be lost to the inert panel, so
  // hand it back to the selection's graph element (or the graph itself).
  const close = useCallback(() => {
    const aside = asideRef.current;
    const hadFocus = aside !== null && aside.contains(document.activeElement);
    const sel = selected;
    onSelect(null);
    if (!hadFocus || !sel) return;
    const target = returnFocusTo?.(sel) ?? null;
    target?.focus();
  }, [selected, onSelect, returnFocusTo]);

  // Esc closes only the topmost layer: not from a field, during IME composition,
  // or while a popup is open; a handler that already took the key wins too.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented || e.isComposing) return;
      if (isEditable(e.target)) return;
      if (document.querySelector(OPEN_POPUP)) return;
      close();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, close]);

  // Auto-pan once per new selection, and again when the graph comes back into
  // view (cards -> graph), so both endpoints stay clear of the drawer.
  const key = selectionKey(selected);
  const canPan = panToKeepVisible !== undefined;
  useEffect(() => {
    if (!selected || !panToKeepVisible || !anchorsFor) return;
    const pts = anchorsFor(selected);
    if (pts.length > 0) panToKeepVisible(pts, DRAWER_WIDTH);
    // Only a new selection or a return to the graph pans, not a refetch that rebuilds the callbacks.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, canPan]);

  const lookups = useMemo(() => {
    const docs = new Map((graph?.documents ?? []).map((d) => [d.id, d]));
    const domains = new Map((graph?.domains ?? []).map((d) => [d.id, d]));
    return { docs, domains };
  }, [graph]);

  if (!mounted) return null;

  const statusLabel = (s: IdeaStatus) => t(`ideas:graph.status.${s}`, STATUS_FALLBACK[s]);
  const docColor = (docId: number | null) => {
    if (docId === null) return "var(--foreground)";
    const first = lookups.docs.get(docId)?.domains[0];
    return domainColorVar(first === undefined ? null : (lookups.domains.get(first)?.color_index ?? null));
  };
  const docTitle = (docId: number) => lookups.docs.get(docId)?.title ?? "";
  const ideaRow = (i: Idea) => (
    <Row
      key={i.id}
      color={docColor(i.source_doc)}
      title={i.title}
      sub={t("ideas:drawer.from", "From {{title}}", { title: docTitle(i.source_doc) })}
      onClick={() => onSelect({ kind: "idea", id: i.id })}
    />
  );
  const none = <p className="text-sm text-muted-foreground">{t("ideas:drawer.none", "None yet")}</p>;

  let kind = "";
  let content: ReactNode = (
    <p className="text-sm text-muted-foreground">{t("ideas:drawer.missing", "This item is no longer in the graph.")}</p>
  );
  const sel = shown;
  if (sel?.kind === "idea") {
    // The selected idea may be hidden by the filter; look it up in the whole graph.
    const idea = graph?.ideas.find((i) => i.id === sel.id);
    if (idea) {
      kind = t("ideas:drawer.ideaKind", "Cross-domain idea · {{status}}", { status: statusLabel(idea.status) });
      content = (
        <>
          <h3 className="mb-2.5 text-[15px] leading-snug font-semibold">{idea.title}</h3>
          <Label>{t("ideas:drawer.source", "Mechanism source")}</Label>
          {/* The cards jump to their document (or the problem) on the graph. */}
          <Row
            color={docColor(idea.source_doc)}
            title={docTitle(idea.source_doc)}
            sub={sectionLabel(idea.source_section)}
            onClick={() => onSelect({ kind: "doc", id: idea.source_doc })}
          />
          <Label>{t("ideas:drawer.target", "Applied to")}</Label>
          {idea.target_doc === null ? (
            <Row
              color={docColor(null)}
              title={graph?.problem?.text ?? t("ideas:graph.problem", "Your problem")}
              sub={t("ideas:drawer.yourProblem", "The problem you described")}
              onClick={() => onSelect({ kind: "problem" })}
            />
          ) : (
            <Row
              color={docColor(idea.target_doc)}
              title={
                <>
                  {docTitle(idea.target_doc)}
                  {lookups.docs.get(idea.target_doc)?.is_private && (
                    <Lock
                      className="ml-1 inline size-3.5 align-[-2px] text-muted-foreground"
                      aria-label={t("ideas:graph.private", "Private document")}
                    />
                  )}
                </>
              }
              sub={idea.target_section != null ? sectionLabel(idea.target_section) : undefined}
              onClick={() => {
                const target = idea.target_doc;
                if (target !== null) onSelect({ kind: "doc", id: target });
              }}
            />
          )}
          <Label>{t("ideas:drawer.why", "Why it fits")}</Label>
          <p className="text-sm leading-relaxed">{idea.rationale}</p>
          {/* With the editor slot, the note is edited there instead. */}
          {idea.note && !slots?.idea && (
            <>
              <Label>{t("ideas:drawer.note", "Note")}</Label>
              <p className="text-sm whitespace-pre-wrap">{idea.note}</p>
            </>
          )}
          {slots?.idea?.(idea)}
        </>
      );
    }
  } else if (sel?.kind === "doc") {
    const doc = lookups.docs.get(sel.id);
    if (doc) {
      kind = doc.is_private ? t("ideas:drawer.documentPrivate", "Document · Private") : t("ideas:drawer.document", "Document");
      const related = ideas.filter((i) => i.source_doc === doc.id || i.target_doc === doc.id);
      content = (
        <>
          <h3 className="mb-2.5 text-[15px] leading-snug font-semibold">
            {doc.title}
            {doc.is_private && (
              <Lock
                className="ml-1.5 inline size-3.5 align-[-2px] text-muted-foreground"
                aria-label={t("ideas:graph.private", "Private document")}
              />
            )}
          </h3>
          {/* With the editor slot, the tags are edited there instead. */}
          {slots?.doc ? (
            slots.doc(doc)
          ) : (
            <>
              <Label>{t("ideas:drawer.domains", "Domains")}</Label>
              <div className="flex flex-wrap gap-1.5">
                {doc.domains.map((id) => {
                  const d = lookups.domains.get(id);
                  if (!d) return null;
                  return (
                    <span key={id} className="inline-flex h-6 items-center gap-1.5 rounded-md bg-secondary px-2 text-xs">
                      <span aria-hidden="true" className="size-2 rounded-full" style={{ background: domainColorVar(d.color_index) }} />
                      {d.name}
                    </span>
                  );
                })}
              </div>
            </>
          )}
          <Label>{t("ideas:drawer.relatedIdeas", "Related ideas")}</Label>
          {related.length ? related.map(ideaRow) : none}
        </>
      );
    }
  } else if (sel?.kind === "domain") {
    const domain = lookups.domains.get(sel.id);
    if (domain) {
      kind = t("ideas:drawer.domain", "Domain");
      const docs = (graph?.documents ?? []).filter((d) => d.domains.includes(domain.id));
      content = (
        <>
          <h3 className="mb-2.5 flex items-center gap-2 text-[15px] leading-snug font-semibold">
            <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: domainColorVar(domain.color_index) }} />
            {domain.name}
          </h3>
          {slots?.domain?.(domain)}
          <Label>{t("ideas:drawer.domainDocs", "Documents ({{count}})", { count: docs.length })}</Label>
          {docs.length
            ? docs.map((d) => (
                <Row
                  key={d.id}
                  color={docColor(d.id)}
                  title={d.title}
                  onClick={() => onSelect({ kind: "doc", id: d.id })}
                />
              ))
            : none}
        </>
      );
    }
  } else if (sel?.kind === "problem") {
    const problem = graph?.problem;
    if (problem) {
      kind = t("ideas:drawer.problem", "Your problem");
      const own = ideas.filter((i) => i.target_doc === null);
      content = (
        <>
          <h3 className="mb-2.5 text-[15px] leading-snug font-semibold whitespace-pre-wrap">{problem.text}</h3>
          <Label>{t("ideas:drawer.tried", "Already tried (won't be suggested again)")}</Label>
          <p className="text-sm whitespace-pre-wrap">
            {problem.tried || <span className="text-muted-foreground">{t("ideas:drawer.triedNone", "Nothing listed")}</span>}
          </p>
          {slots?.problem?.(problem)}
          <Label>{t("ideas:drawer.problemIdeas", "Ideas for this problem ({{count}})", { count: own.length })}</Label>
          {own.length ? own.map(ideaRow) : none}
        </>
      );
    }
  }

  const slidIn = open && entered;
  return (
    <aside
      ref={asideRef}
      data-testid="idea-drawer"
      data-state={state}
      aria-label={t("ideas:drawer.aria", "Details")}
      inert={!open}
      // Clicks inside the drawer never reach the graph behind it.
      onClick={(e) => e.stopPropagation()}
      className={cn(
        "absolute top-0 right-0 bottom-0 z-20 w-[340px] overflow-y-auto border-l bg-background p-4",
        "transition-transform motion-reduce:transition-none",
        slidIn
          ? "translate-x-0 shadow-lg duration-[240ms] ease-[cubic-bezier(.2,.8,.2,1)]"
          : "translate-x-full duration-200 ease-[cubic-bezier(.4,0,.6,1)]",
      )}
    >
      <div className="-mt-1 -mr-1.5 mb-1.5 flex items-center justify-between gap-2">
        <span className="text-xs text-muted-foreground">
          {sel?.kind === "idea" && <Lightbulb className="mr-1 inline size-3.5 align-[-2px]" aria-hidden="true" />}
          {kind}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label={t("ideas:drawer.close", "Close")}
          title={t("ideas:drawer.close", "Close")}
          onClick={close}
        >
          <X aria-hidden="true" />
        </Button>
      </div>
      {/* Swapping selection fades the new content in. */}
      <div key={selectionKey(sel)} className="animate-in fade-in-0 duration-150 motion-reduce:animate-none">
        {content}
      </div>
    </aside>
  );
}
