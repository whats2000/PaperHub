import { useEffect, useState, type ReactNode } from "react";
import {
  Bookmark,
  BookmarkX,
  BookOpen,
  ChevronDown,
  Circle,
  CircleSlash,
  Clock,
  CornerDownRight,
  Database,
  FileImage,
  FileMinus,
  FilePlus,
  Globe,
  GraduationCap,
  Image,
  Images,
  Layers,
  Lightbulb,
  ListTree,
  Loader2,
  NotebookPen,
  PenLine,
  Pencil,
  Presentation,
  ScanText,
  Search,
  Sparkles,
  Table,
  TextSearch,
  X,
  ZoomIn,
  type LucideIcon,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { Collapsible } from "@/components/ui/collapsible";
import {
  formatDuration,
  searchQueries,
  type NodeState,
  type SearchQuery,
  type TimelineNode,
  type ToolNode,
} from "@/lib/activityTimeline";
import { prefersReducedMotion } from "@/lib/motion";
import { useDestinationLabel } from "@/lib/outboundDestination";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";

type TFn = ReturnType<typeof useTranslation>["t"];
type Obj = Record<string, unknown>;

/** Every tool in the agent registry (agents/core/*), with its node icon. */
const ICONS: Record<string, LucideIcon> = {
  list_sections: ListTree,
  read_section: BookOpen,
  read_chunks: BookOpen,
  search_chunks: TextSearch,
  list_figures_tables: Images,
  read_layout_object: Table,
  view_figure: Image,
  view_page: FileImage,
  zoom: ZoomIn,
  view_chunk: ScanText,
  gather: Layers,
  query_library: Database,
  search_papers: Search,
  add_paper: FilePlus,
  remember: Bookmark,
  forget: BookmarkX,
  remove_paper: FileMinus,
  list_ideas: Lightbulb,
  propose_ideas: Sparkles,
  create_deck: Presentation,
  edit_slides: Pencil,
  write_notes: NotebookPen,
};

const str = (v: unknown): string =>
  typeof v === "string" || typeof v === "number" || typeof v === "boolean" ? String(v) : "";
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const obj = (v: unknown): Obj =>
  v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};

/** A paper of this chat by id, named by its title. */
function usePaperName(sessionId: number): (id: unknown) => string {
  const { t } = useTranslation("chat");
  const refs = useChatStore((s) => s.referencesBySession[sessionId]);
  return (id: unknown) => {
    const ref = refs?.find((r) => r.paper_content_id === Number(id));
    return ref?.title ?? t("trace.tl.unknownPaper", "paper {{id}}", { id: str(id) });
  };
}

interface NodeView {
  icon: LucideIcon;
  title: string;
  sub: ReactNode;
  /** The card to jump to (a waiting node). */
  card: string | null;
  queries: SearchQuery[] | null;
  lookups: string[];
  state: NodeState;
}

/** The node in plain language (the prototype's `nodeView`). */
function nodeView(n: ToolNode, t: TFn, paper: (id: unknown) => string): NodeView {
  const a = n.args;
  const res = n.result;
  const v: NodeView = {
    icon: ICONS[n.name] ?? Circle,
    title: n.name,
    sub: null,
    card: n.cards[0]?.id ?? null,
    queries: null,
    lookups: [],
    state: n.state,
  };
  const waiting = n.state === "waiting";
  const stopped = n.skippedBy === "stopped";
  const tt = (key: string, fallback: string, values: Obj = {}) =>
    t(`trace.tl.title.${key}`, fallback, values);
  switch (n.name) {
    case "list_sections":
      v.title = tt("list_sections", "Listed the sections of {{paper}}", { paper: paper(a.paper) });
      break;
    case "read_section":
      v.title = tt("read_section", "Read “{{section}}” in {{paper}}", {
        section: str(a.section),
        paper: paper(a.paper),
      });
      if (n.row) {
        v.sub = t("trace.tl.sub.passages", "{{count}} passages", {
          count: arr(res.chunk_ids).length,
        });
      }
      break;
    case "read_chunks":
      v.title = tt("read_chunks", "Read {{count}} passages", { count: arr(a.ids).length });
      break;
    case "search_chunks":
      v.title = tt("search_chunks", "Searched this chat's papers for “{{query}}”", {
        query: str(a.query),
      });
      if (n.row) {
        v.sub = t("trace.tl.sub.hits", "{{count}} hits", { count: arr(res.hit_ids).length });
      }
      break;
    case "list_figures_tables":
      v.title = tt("list_figures_tables", "Listed the figures and tables of {{paper}}", {
        paper: paper(a.paper),
      });
      break;
    case "read_layout_object":
      v.title = tt("read_layout_object", "Read {{label}} in {{paper}}", {
        label: str(a.label),
        paper: paper(a.paper),
      });
      break;
    case "view_figure":
      v.title = tt("view_figure", "Looked at {{figure}} in {{paper}}", {
        figure: str(a.figure),
        paper: paper(a.paper),
      });
      break;
    case "view_page":
      v.title = tt("view_page", "Looked at page {{page}} of {{paper}}", {
        page: str(a.page),
        paper: paper(a.paper),
      });
      break;
    case "zoom":
      v.title = tt("zoom", "Zoomed into page {{page}} of {{paper}}", {
        page: str(a.page),
        paper: paper(a.paper),
      });
      break;
    case "view_chunk":
      v.title = tt("view_chunk", "Looked at passage {{id}} on its page", { id: str(a.id) });
      break;
    case "gather":
      v.title = tt("gather", "Gathered passages from {{count}} papers", {
        count: arr(a.papers).length,
      });
      if (n.row) {
        v.sub = t("trace.tl.sub.passages", "{{count}} passages", {
          count: arr(res.chunk_ids_selected).length,
        });
      }
      break;
    case "query_library":
      v.title = tt("query_library", "Asked your library: “{{question}}”", {
        question: str(a.question),
      });
      break;
    case "add_paper": {
      const title = str(res.title) || str(a.paper_id);
      v.title =
        n.state === "failed" || res.added === false
          ? tt("add_paper_failed", "Add {{title}} to this chat", { title })
          : tt("add_paper", "Added {{title}} to this chat", { title });
      break;
    }
    case "remember":
      v.title = tt("remember", "Saved to memory: “{{fact}}”", { fact: str(a.fact) });
      break;
    case "forget":
      v.title =
        n.state === "done"
          ? tt("forget", "Forgot memory {{id}}", { id: str(a.memory_id) })
          : tt("forget_pending", "Forget memory {{id}}", { id: str(a.memory_id) });
      break;
    case "remove_paper":
      v.title =
        n.state === "done"
          ? tt("remove_paper", "Removed “{{paper}}” from this chat", { paper: paper(a.paper) })
          : tt("remove_paper_pending", "Remove “{{paper}}” from this chat", {
              paper: paper(a.paper),
            });
      break;
    case "list_ideas":
      v.title = tt("list_ideas", "Listed ideas");
      break;
    case "propose_ideas":
      v.title = tt("propose_ideas", "Started idea generation for “{{problem}}”", {
        problem: str(a.problem),
      });
      break;
    case "create_deck":
      v.title = tt("create_deck", "Built a slide deck");
      break;
    case "edit_slides":
      v.title = tt("edit_slides", "Edited the slides");
      break;
    case "write_notes":
      v.title = tt("write_notes", "Wrote speaker notes");
      break;
    case "search_papers":
      return searchView(n, v, t);
    default:
      break; // an unknown tool keeps its name
  }
  if (n.name === "remove_paper" || n.name === "forget") {
    const confirm = obj(res.confirm);
    const approve = n.children.find((c) => c.tool === "agent:approve");
    if (confirm.decision === "confirmed") v.sub = t("trace.tl.sub.confirmed", "You confirmed it.");
    else if (n.state === "done" && obj(approve?.result_summary_json).decision === "run") {
      v.sub = t("trace.tl.sub.autoApproved", "Auto-approved.");
    }
  }
  if (waiting) v.sub = <Attn>{t("trace.tl.sub.waiting", "Waiting for your OK.")}</Attn>;
  else if (stopped) v.sub = t("trace.tl.sub.stopped", "Not run: the turn was stopped.");
  else if (n.skippedBy === "dismissed")
    v.sub = t("trace.tl.sub.dismissed", "Not done: you dismissed it.");
  else if (n.skippedBy === "pending")
    v.sub = t("trace.tl.sub.pending", "Not run: it waited for your OK.");
  return v;
}

function searchView(n: ToolNode, v: NodeView, t: TFn): NodeView {
  const res = n.result;
  const query = str(res.query) || str(n.args.query);
  const title = (key: string, fallback: string) => t(`trace.tl.title.${key}`, fallback, { query });
  const askCard = n.cards.find((c) => c.kind === "search");
  if (askCard) {
    // Ask first: nothing has left yet.
    v.title = title("search_papers_pending", "Search papers for “{{query}}”");
    v.sub = (
      <Attn>{t("trace.tl.sub.waitingSearch", "Waiting for your OK before anything is sent.")}</Attn>
    );
    v.card = askCard.id;
    return v;
  }
  if (n.skippedBy === "declined") {
    v.title = title("search_papers_pending", "Search papers for “{{query}}”");
    const note = str(res.note);
    v.sub = note
      ? t("trace.tl.sub.declinedNote", "Not searched. You told the agent: “{{note}}”", { note })
      : t("trace.tl.sub.declined", "Not searched: you didn't allow it.");
    return v;
  }
  if (n.skippedBy === "stopped" && n.children.every((c) => c.tool !== "outbound:review")) {
    v.title = title("search_papers_pending", "Search papers for “{{query}}”");
    v.sub = t("trace.tl.sub.stoppedSearch", "Not searched: the turn was stopped.");
    return v;
  }
  v.title =
    n.row === null
      ? title("search_papers_running", "Searching papers for “{{query}}”")
      : title("search_papers", "Searched papers for “{{query}}”");
  const { queries, lookups } = searchQueries(n.children, n.skippedBy === "stopped");
  // A stopped search that already sent queries reports what it did.
  if (n.skippedBy === "stopped") v.state = "done";
  const count = (o: SearchQuery["outcome"]) => queries.filter((q) => q.outcome === o).length;
  const sent = count("sent") + count("rewritten") + count("timeout");
  const rewritten = queries.filter(
    (q) => q.outcome === "rewritten" || (q.outcome === "timeout" && q.sent),
  ).length;
  const notSent = count("blocked") + count("declined") + count("stopped");
  const held = count("held");
  const parts: ReactNode[] = [];
  if (obj(res.ask).edited === true) {
    parts.push(t("trace.tl.sub.allowedEdited", "You edited the query before allowing it."), " ");
  }
  if (n.row && Array.isArray(res.candidates)) {
    parts.push(
      t("trace.tl.sub.found", "Found {{count}} papers.", { count: res.candidates.length }),
      " ",
    );
  }
  const bits: ReactNode[] = [
    t("trace.tl.sub.sent", "{{count}} queries sent", { count: sent }) +
      (rewritten
        ? ` ${t("trace.tl.sub.rewritten", "({{count}} rewritten first)", { count: rewritten })}`
        : ""),
  ];
  if (notSent)
    bits.push(
      <Bad key="ns">{t("trace.tl.sub.notSent", "{{count}} not sent", { count: notSent })}</Bad>,
    );
  if (count("timeout")) {
    bits.push(
      <Attn key="to">
        {t("trace.tl.sub.timedOut", "{{count}} timed out", { count: count("timeout") })}
      </Attn>,
    );
  }
  if (held) {
    bits.push(
      <Attn key="h">
        {t("trace.tl.sub.heldWaiting", "{{count}} waiting for your OK", { count: held })}
      </Attn>,
    );
  }
  bits.forEach((b, i) => parts.push(i > 0 ? ", " : "", b));
  parts.push(".");
  v.sub = (
    <>
      {parts.map((p, i) => (
        <span key={i}>{p}</span>
      ))}
    </>
  );
  const heldCard = n.cards.find((c) => c.kind === "held");
  if (heldCard) v.card = heldCard.id;
  v.queries = queries;
  v.lookups = lookups;
  return v;
}

function Attn({ children }: { children: ReactNode }) {
  return <span className="text-amber-700 dark:text-amber-300">{children}</span>;
}
function Bad({ children }: { children: ReactNode }) {
  return <span className="text-destructive">{children}</span>;
}

function queryWhy(q: SearchQuery, t: TFn): string {
  switch (q.outcome) {
    case "rewritten":
      return t("trace.tl.q.rewritten", "Rewritten before sending: {{reason}}", {
        reason: (q.reason ?? "").replace(/\.$/, ""),
      });
    case "blocked":
      return q.term
        ? t("trace.tl.q.blockedTerm", "Not sent: contains the sensitive term {{term}}", {
            term: q.term,
          })
        : t("trace.tl.q.blocked", "Not sent: blocked");
    case "held":
      return t("trace.tl.q.held", "Waiting for your OK: {{reason}}", { reason: q.reason ?? "" });
    case "declined":
      return t("trace.tl.q.declined", "Not sent: you chose not to");
    case "stopped":
      return t("trace.tl.q.stopped", "Not sent: the turn was stopped");
    case "timeout":
      return t("trace.tl.q.timeout", "The search service timed out");
    default:
      return q.approved === "edited"
        ? t("trace.tl.q.sentAfterEdit", "Sent after your edit")
        : q.approved === "ok"
          ? t("trace.tl.q.sentAfterOk", "Sent after your OK")
          : t("trace.tl.q.sent", "Sent");
  }
}

function QueryRow({ q }: { q: SearchQuery }) {
  const { t } = useTranslation("chat");
  const dest = useDestinationLabel(q.destination);
  const Icon = q.destination === "api.semanticscholar.org" ? GraduationCap : Globe;
  return (
    <li data-o={q.outcome} className="grid grid-cols-[14px_minmax(0,1fr)] gap-x-1.5 text-xs">
      <Icon className="mt-0.5 h-3 w-3 text-muted-foreground" aria-hidden />
      <span className="font-mono text-foreground [overflow-wrap:anywhere]">
        {q.sent ? (
          <>
            {q.sent}
            <br />
            <del className="text-muted-foreground">{q.text}</del>
          </>
        ) : (
          q.text
        )}
      </span>
      <span
        className={cn(
          "col-start-2 text-muted-foreground",
          (q.outcome === "held" || q.outcome === "timeout") && "text-amber-700 dark:text-amber-300",
          (q.outcome === "blocked" || q.outcome === "declined" || q.outcome === "stopped") &&
            "text-destructive",
        )}
      >
        {dest} · {queryWhy(q, t)}
      </span>
    </li>
  );
}

/** Seconds since the running node appeared (the live beat has no clock). */
function useElapsed(running: boolean): number {
  const [start] = useState(() => Date.now());
  const [now, setNow] = useState(start);
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [running]);
  return Math.max(0, Math.round((now - start) / 1000));
}

const MARK: Record<NodeState, string> = {
  done: "",
  running: "",
  waiting:
    "border-amber-300 bg-amber-50 text-amber-700 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-300",
  skipped: "",
  failed: "border-destructive text-destructive",
};

function Mark({ state, icon: Icon }: { state: NodeState; icon: LucideIcon }) {
  const Shown =
    state === "waiting"
      ? Clock
      : state === "failed"
        ? X
        : state === "skipped"
          ? CircleSlash
          : state === "running"
            ? Loader2
            : Icon;
  return (
    <span
      className={cn(
        "row-span-4 grid h-5 w-5 place-items-center rounded-full border border-border bg-background text-muted-foreground",
        MARK[state],
      )}
    >
      <Shown
        className={cn(
          "h-[11px] w-[11px]",
          state === "running" && "animate-spin motion-reduce:animate-none",
          state === "waiting" && "motion-safe:animate-pulse",
        )}
        aria-hidden
      />
    </span>
  );
}

function ToolNodeItem({ n, sessionId }: { n: ToolNode; sessionId: number }) {
  const { t } = useTranslation("chat");
  const paper = usePaperName(sessionId);
  const [open, setOpen] = useState(false);
  const v = nodeView(n, t, paper);
  const elapsed = useElapsed(n.row === null && v.state === "running");
  const time =
    v.state === "waiting"
      ? ""
      : n.row
        ? formatDuration(n.row.latency_ms)
        : v.state === "running" && elapsed > 0
          ? `${elapsed} s`
          : "";
  const step = n.step;
  const stepNo = obj(step?.args_redacted_json).step;
  const model = str(obj(step?.result_summary_json).model) || str(step?.model);
  const internal = [...new Set(n.children.map((c) => c.tool))];
  return (
    <li
      data-state={v.state}
      data-tool={n.name}
      className="relative grid grid-cols-[20px_minmax(0,1fr)_auto] gap-x-2.5 pb-2.5 last:pb-0 before:absolute before:top-[22px] before:bottom-0.5 before:left-[9.5px] before:w-px before:bg-border last:before:hidden"
    >
      <Mark state={v.state} icon={v.icon} />
      <span
        className={cn(
          "min-w-0 text-[13px] leading-5 text-foreground [overflow-wrap:anywhere]",
          v.state === "skipped" && "text-muted-foreground",
        )}
      >
        {v.title}
      </span>
      <span className="text-xs leading-5 whitespace-nowrap text-muted-foreground tabular-nums">
        {time}
      </span>
      {v.sub && <div className="col-span-2 col-start-2 text-xs text-muted-foreground">{v.sub}</div>}
      <div className="col-span-2 col-start-2 mt-0.5 flex gap-3">
        {v.card && (
          <button
            type="button"
            onClick={() => useNavStore.getState().setFocusHeldId(v.card)}
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <CornerDownRight className="h-3 w-3" aria-hidden />
            {t("trace.tl.goToCard", "Go to card")}
          </button>
        )}
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <ChevronDown
            className={cn(
              "h-3 w-3 transition-transform duration-200 motion-reduce:transition-none",
              !open && "-rotate-90",
            )}
            aria-hidden
          />
          {v.queries
            ? t("trace.tl.queriesDetails", "Queries and details")
            : t("trace.tl.details", "Details")}
        </button>
      </div>
      <Collapsible
        open={open}
        className="col-span-2 col-start-2"
        onExpanded={(el) =>
          el.scrollIntoView?.({
            block: "nearest",
            behavior: prefersReducedMotion() ? "auto" : "smooth",
          })
        }
      >
        {v.queries && v.queries.length > 0 && (
          <ul className="mt-1.5 flex flex-col gap-1.5" data-testid="node-queries">
            {v.queries.map((q, i) => (
              <QueryRow key={i} q={q} />
            ))}
          </ul>
        )}
        {v.lookups.length > 0 && (
          <p className="mt-1.5 text-xs text-muted-foreground">
            {t(
              "trace.tl.lookups",
              "Also looked up {{count}} found papers by ID ({{ids}}). ID lookups are sent without review.",
              { count: v.lookups.length, ids: v.lookups.join(", ") },
            )}
          </p>
        )}
        <dl className="mt-2 grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          <dt>{t("trace.tl.d.tool", "Tool")}</dt>
          <dd className="font-mono [overflow-wrap:anywhere]">{n.row?.tool ?? `tool:${n.name}`}</dd>
          {typeof stepNo === "number" && (
            <>
              <dt>{t("trace.tl.d.calledIn", "Called in")}</dt>
              <dd className="font-mono [overflow-wrap:anywhere]">
                {t("trace.tl.d.calledInValue", "agent step {{n}} · {{model}}", {
                  n: stepNo,
                  model: model || "-",
                })}
              </dd>
            </>
          )}
          <dt>{t("trace.tl.d.status", "Status")}</dt>
          <dd className="font-mono">
            {n.row ? n.row.status : v.state}
            {n.row?.error ? ` · ${n.row.error}` : ""}
            {n.row && n.row.latency_ms ? ` · ${formatDuration(n.row.latency_ms)}` : ""}
          </dd>
          {n.children.length > 0 && (
            <>
              <dt>{t("trace.tl.d.internal", "Internal steps")}</dt>
              <dd className="font-mono [overflow-wrap:anywhere]">
                {n.children.length} ({internal.join(", ")})
              </dd>
            </>
          )}
        </dl>
      </Collapsible>
    </li>
  );
}

function AnswerItem({
  running,
  latency,
  writing,
}: {
  running: boolean;
  latency: number;
  writing: boolean;
}) {
  const { t } = useTranslation("chat");
  return (
    <li
      data-state={running ? "running" : "done"}
      data-tool="answer"
      className="relative grid grid-cols-[20px_minmax(0,1fr)_auto] gap-x-2.5"
    >
      <Mark state={running ? "running" : "done"} icon={PenLine} />
      <span className="text-[13px] leading-5 text-foreground">
        {running
          ? writing
            ? t("trace.tl.writing", "Writing the answer…")
            : t("trace.tl.thinking", "Thinking…")
          : t("trace.tl.answer", "Wrote the answer")}
      </span>
      <span className="text-xs leading-5 whitespace-nowrap text-muted-foreground tabular-nums">
        {running ? "" : formatDuration(latency)}
      </span>
    </li>
  );
}

/**
 * The activity timeline (FR-02, H5a wave 3; prototype
 * `designs/2026-11-13-plan-h5a/`): one node per tool call in plain language
 * with a state marker (done / running / waiting with "Go to card" /
 * skipped / failed) and its duration; a node's Details (and a search's
 * queries) expand by height (200 ms) and scroll into view.
 */
export function ActivityTimeline({
  nodes,
  sessionId,
  writing,
}: {
  nodes: TimelineNode[];
  sessionId: number;
  writing: boolean;
}) {
  return (
    <ol className="mt-2 mb-0.5 list-none p-0" data-testid="activity-timeline">
      {nodes.map((n) =>
        n.kind === "answer" ? (
          <AnswerItem
            key={n.key}
            running={n.running}
            latency={n.row?.latency_ms ?? 0}
            writing={writing}
          />
        ) : (
          <ToolNodeItem key={n.key} n={n} sessionId={sessionId} />
        ),
      )}
    </ol>
  );
}
