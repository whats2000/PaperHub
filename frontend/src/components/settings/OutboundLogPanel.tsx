import {
  Check,
  Clock,
  Download,
  MessageSquare,
  MessageSquareShare,
  PenLine,
  ShieldQuestion,
  ShieldX,
  type LucideIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  discardHeld,
  getHeldSearches,
  getOutboundLog,
  getOutboundLogCsv,
  sendHeld,
  type HeldSearch,
  type OutboundDecision,
  type OutboundLogFilters,
  type OutboundLogRow,
} from "../../lib/api";
import { openChatAtHeld } from "../../lib/heldJump";
import { prefersReducedMotion } from "../../lib/motion";
import { cn } from "../../lib/utils";
import { useChatStore } from "../../store/chat";
import { useNavStore } from "../../store/nav";
import { Button } from "../ui/button";

type LoadError = "invalid" | "failed" | null;

type Tab = "all" | "held";
type Period = "d30" | "d7" | "today";

const EXIT_MS = 200;
const DECISIONS: OutboundDecision[] = [
  "sent",
  "rewritten",
  "asked_sent",
  "asked_discarded",
  "blocked",
  "held",
];
const STAGES = new Set([
  "disabled",
  "allowlist",
  "exact_id",
  "terms",
  "cache",
  "review",
  "timeout",
  "error",
  "user",
  "span",
  "llm",
  "system",
]);

const DECISION_VIEW: Record<OutboundDecision, { Icon: LucideIcon; tone: string }> = {
  sent: { Icon: Check, tone: "text-foreground" },
  rewritten: { Icon: PenLine, tone: "text-foreground" },
  asked_sent: { Icon: ShieldQuestion, tone: "text-amber-700 dark:text-amber-300" },
  asked_discarded: { Icon: ShieldQuestion, tone: "text-amber-700 dark:text-amber-300" },
  blocked: { Icon: ShieldX, tone: "text-destructive" },
  held: { Icon: Clock, tone: "text-amber-700 dark:text-amber-300" },
};

const FADE_IN = "animate-in fade-in-0 duration-200 motion-reduce:animate-none";
const COLLAPSE =
  "grid grid-rows-[1fr] opacity-100 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none";

function sinceFor(period: Period): string {
  const d = new Date();
  if (period === "today") d.setHours(0, 0, 0, 0);
  else d.setTime(d.getTime() - (period === "d7" ? 7 : 30) * 86_400_000);
  return d.toISOString();
}

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** The HTTP status carried by an `apiFetch` error message ("API 409: ..."). */
function statusOf(e: unknown): number | null {
  const m = /^API (\d{3})/.exec(e instanceof Error ? e.message : "");
  return m ? Number(m[1]) : null;
}

/**
 * Settings > Outbound log (SRS §III-10.5): everything PaperHub sent out, plus
 * the searches held for the user's confirmation.
 */
export function OutboundLogPanel({ initialTab = "all" }: { initialTab?: Tab }) {
  const { t } = useTranslation("outbound");
  const sessions = useChatStore((s) => s.sessions);
  const setHeldList = useNavStore((s) => s.setHeldList);

  const [tab, setTab] = useState<Tab>(initialTab);
  const [decision, setDecision] = useState("");
  const [destination, setDestination] = useState("");
  const [period, setPeriod] = useState<Period>("d30");
  const [rows, setRows] = useState<OutboundLogRow[] | null>(null);
  const [seenDests, setSeenDests] = useState<string[]>([]);
  const [heldRows, setHeldRows] = useState<HeldSearch[]>([]);
  const [leaving, setLeaving] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [logError, setLogError] = useState<LoadError>(null);
  const [heldError, setHeldError] = useState<LoadError>(null);
  const [logTry, setLogTry] = useState(0);
  const seq = useRef(0);
  const timers = useRef<number[]>([]);
  useEffect(() => {
    const list = timers.current;
    return () => list.forEach((id) => window.clearTimeout(id));
  }, []);

  const filters = useCallback(
    (): OutboundLogFilters => ({
      decision: decision || undefined,
      destination: destination || undefined,
      since: sinceFor(period),
    }),
    [decision, destination, period],
  );

  useEffect(() => {
    const mine = ++seq.current;
    getOutboundLog(filters()).then(
      (r) => {
        if (mine !== seq.current) return;
        setLogError(null);
        setRows(r);
        setSeenDests((prev) => {
          const next = new Set(prev);
          for (const x of r) next.add(x.destination);
          return next.size === prev.length ? prev : [...next].sort();
        });
      },
      (e: unknown) => {
        if (mine === seq.current) {
          setRows([]);
          setLogError(statusOf(e) === 422 ? "invalid" : "failed");
        }
      },
    );
  }, [filters, logTry]);

  const heldLoaded = useRef(false);
  const loadHeld = useCallback(() => {
    getHeldSearches().then(
      (r) => {
        heldLoaded.current = true;
        setHeldError(null);
        setHeldRows(r);
      },
      () => setHeldError("failed"),
    );
  }, []);
  useEffect(loadHeld, [loadHeld]);
  // The nav badge follows the list itself (never a count read from a stale
  // closure while two removals overlap); not before the first load.
  useEffect(() => {
    if (heldLoaded.current) setHeldList(heldRows);
  }, [heldRows, setHeldList]);

  const exportCsv = async () => {
    try {
      const blob = await getOutboundLogCsv(filters());
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "outbound-log.csv";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      toast.error(t("exportFailed", "Couldn't export the log"));
    }
  };

  /** Collapse these rows out, then drop them from the list. */
  const removeRows = (ids: string[]) => {
    setLeaving((prev) => new Set([...prev, ...ids]));
    const timer = window.setTimeout(
      () => {
        setHeldRows((prev) => prev.filter((h) => !ids.includes(h.id)));
        setLeaving((prev) => {
          const next = new Set(prev);
          for (const id of ids) next.delete(id);
          return next;
        });
      },
      prefersReducedMotion() ? 0 : EXIT_MS,
    );
    timers.current.push(timer);
  };

  /** Open a chat (and, with a held id, land on that card). */
  const openChat = (sessionId: number, heldId?: string) => {
    if (!openChatAtHeld(sessionId, heldId)) {
      toast.error(t("held.chatGone", "That chat isn't on this device or was deleted."));
    }
  };

  const withBusy = async (id: string, fn: () => Promise<void>) => {
    setBusy((p) => new Set(p).add(id));
    try {
      await fn();
    } finally {
      setBusy((p) => {
        const n = new Set(p);
        n.delete(id);
        return n;
      });
    }
  };

  const send = (h: HeldSearch) =>
    withBusy(h.id, async () => {
      try {
        const r = await sendHeld(h.id);
        removeRows(r.held_ids.length > 0 ? r.held_ids : [h.id]);
        toast(t("held.sent", "Sent."));
      } catch (e) {
        const msg = e instanceof Error ? e.message : "";
        const status = statusOf(e);
        toast.error(
          msg.includes("no_resender")
            ? t(
                "held.noResender",
                "Nothing can send this search right now. Discard it, or try again later.",
              )
            : status === 409
            ? t("held.stale", "This search was already handled.")
            : msg.includes("sensitive_term")
              ? t("held.sensitive", "It contains a sensitive term, so it can't be sent.")
              : msg.includes("empty_text")
                ? t("held.emptyText", "There is nothing to send.")
                : t("held.failed", "Couldn't send it. Try again."),
        );
        if (status === 409 && !msg.includes("no_resender")) loadHeld();
      }
    });

  const discard = (h: HeldSearch) =>
    withBusy(h.id, async () => {
      try {
        const r = await discardHeld(h.id);
        removeRows(r.held_ids.length > 0 ? r.held_ids : [h.id]);
        toast(t("held.discarded", "Discarded. Nothing was sent."));
      } catch (e) {
        toast.error(
          statusOf(e) === 409
            ? t("held.stale", "This search was already handled.")
            : t("held.discardFailed", "Couldn't discard it. Try again."),
        );
        if (statusOf(e) === 409) loadHeld();
      }
    });

  /** The chat as a link that opens it; plain text when there is none here. */
  const chatLink = (sid: number | null) => {
    if (sid == null) return <span>{t("noChat", "No chat")}</span>;
    const title = sessions.find((s) => s.backend_session_id === sid)?.title;
    if (title === undefined) return <span>{`#${sid}`}</span>;
    return (
      <button
        type="button"
        onClick={() => openChat(sid)}
        className="inline-flex min-w-0 items-center gap-1 text-muted-foreground underline decoration-border underline-offset-2 transition-colors hover:text-foreground hover:decoration-current"
      >
        <MessageSquare className="size-3 shrink-0" aria-hidden="true" />
        <span className="break-words">{title}</span>
      </button>
    );
  };
  const stageLabel = (stage: string): string =>
    STAGES.has(stage) ? t(`stage.${stage}`, stage) : stage;

  /** Line 2: the text; a rewrite's original struck through below it. */
  const sentText = (r: OutboundLogRow) => {
    const notSent = r.decision === "blocked" || r.decision === "held" || r.sent_text == null;
    if (r.kind === "llm") {
      return (
        <span className="mt-1 block text-xs text-muted-foreground">
          {t("contentNotLogged", "(content not logged)")}
        </span>
      );
    }
    if (notSent) {
      return (
        <span className="mt-1 block font-mono text-[11.5px] [overflow-wrap:anywhere]">
          <span>{r.original_text ?? ""}</span>
          <span className="ml-1.5 rounded border border-border px-1 font-sans text-[10px] text-muted-foreground">
            {t("notSent", "Not sent")}
          </span>
        </span>
      );
    }
    return (
      <>
        <span className="mt-1 block font-mono text-[11.5px] [overflow-wrap:anywhere]">
          {r.sent_text}
        </span>
        {r.decision === "rewritten" && r.original_text && (
          <span className="mt-0.5 block font-mono text-[11px] text-muted-foreground line-through [overflow-wrap:anywhere]">
            {r.original_text}
          </span>
        )}
      </>
    );
  };

  const ROW = "min-w-0 border-t border-border px-2.5 py-2 text-sm first:border-t-0";
  const LINE1 = "flex flex-wrap items-center gap-x-2 gap-y-0.5";
  const DEST = "min-w-0 font-medium [overflow-wrap:anywhere]";
  const TIME = "ml-auto whitespace-nowrap text-xs tabular-nums text-muted-foreground";
  const LINE3 = "mt-1 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-muted-foreground";

  const tabBtn = (id: Tab, label: string, count?: number) => (
    <button
      type="button"
      role="tab"
      aria-selected={tab === id}
      onClick={() => setTab(id)}
      className={cn(
        "inline-flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm transition-colors",
        tab === id
          ? "border-primary font-medium text-foreground"
          : "border-transparent text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      {count !== undefined && count > 0 && (
        <span className="rounded-full bg-muted px-1.5 text-[11px] tabular-nums">
          {count}
        </span>
      )}
    </button>
  );

  const selectCls =
    "h-8 rounded-md border border-border bg-background px-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 pb-2">
        <h3 className="text-sm font-semibold">{t("title", "Outbound log")}</h3>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => void exportCsv()}>
          <Download />
          {t("exportCsv", "Export CSV")}
        </Button>
      </div>
      <div role="tablist" className="flex border-b border-border">
        {tabBtn("all", t("tabAll", "Everything sent"))}
        {tabBtn("held", t("tabHeld", "Held"), heldRows.length)}
      </div>

      {tab === "all" ? (
        <div key="all" className={cn("flex-1 pt-3", FADE_IN, "slide-in-from-left-2")}>
          <div className="mb-3 flex flex-wrap gap-2">
            <select
              aria-label={t("filter.decision", "Decision")}
              className={selectCls}
              value={decision}
              onChange={(e) => setDecision(e.target.value)}
            >
              <option value="">{t("filter.allDecisions", "All decisions")}</option>
              {DECISIONS.map((d) => (
                <option key={d} value={d}>
                  {t(`decision.${d}`, d)}
                </option>
              ))}
            </select>
            <select
              aria-label={t("filter.destination", "Destination")}
              className={selectCls}
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
            >
              <option value="">{t("filter.allDestinations", "All destinations")}</option>
              {seenDests.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <select
              aria-label={t("filter.period", "Period")}
              className={selectCls}
              value={period}
              onChange={(e) => setPeriod(e.target.value as Period)}
            >
              <option value="d30">{t("filter.d30", "Last 30 days")}</option>
              <option value="d7">{t("filter.d7", "Last 7 days")}</option>
              <option value="today">{t("filter.today", "Today")}</option>
            </select>
          </div>
          <ul
            aria-label={t("tabAll", "Everything sent")}
            className="rounded-md border border-border"
          >
            {(rows ?? []).map((r) => {
              const { Icon, tone } = DECISION_VIEW[r.decision] ?? DECISION_VIEW.sent;
              return (
                <li key={r.id} className={cn(ROW, FADE_IN)}>
                  <div className={LINE1}>
                    <span className={cn("dec inline-flex items-center gap-1 whitespace-nowrap text-xs", tone)}>
                      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                      <span>{t(`decision.${r.decision}`, r.decision)}</span>
                    </span>
                    <span className={DEST}>{r.destination}</span>
                    <span className={TIME}>{fmtTime(r.created_at)}</span>
                  </div>
                  {sentText(r)}
                  <div className={LINE3}>
                    <span>{stageLabel(r.stage)}</span>
                    {chatLink(r.session_id)}
                  </div>
                </li>
              );
            })}
            {logError && (
              <li role="alert" className={cn(ROW, "py-6 text-center text-muted-foreground", FADE_IN)}>
                <span className="mr-2">
                  {logError === "invalid"
                    ? t("invalidFilter", "The date filter isn't valid.")
                    : t("loadFailed", "Couldn't load the log.")}
                </span>
                <Button size="sm" variant="outline" onClick={() => setLogTry((n) => n + 1)}>
                  {t("retry", "Retry")}
                </Button>
              </li>
            )}
            {!logError && rows !== null && rows.length === 0 && (
              <li className={cn(ROW, "py-6 text-center text-muted-foreground", FADE_IN)}>
                {t("empty", "Nothing matches these filters.")}
              </li>
            )}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">
            {t(
              "footer",
              "Logged by PaperHub for its own requests. Network-level egress control is set up by your deployer.",
            )}
          </p>
        </div>
      ) : (
        <div key="held" className={cn("flex-1 pt-3", FADE_IN, "slide-in-from-right-2")}>
          <p className="mb-3 text-xs text-muted-foreground">
            {t("held.intro", "Searches the review wasn't sure about. Nothing here has been sent.")}
          </p>
          <ul
            aria-label={t("nav.held", "Held searches")}
            className="rounded-md border border-border"
          >
            {heldRows.map((h) => {
              // A chat's search is decided in that chat, where the follow-up runs.
              const inChat = h.session_id != null && !h.unattended;
              return (
                <li
                  key={h.id}
                  data-state={leaving.has(h.id) ? "closed" : "open"}
                  inert={leaving.has(h.id)}
                  className={cn(COLLAPSE, "border-t border-border first:border-t-0")}
                >
                  <div className="min-h-0 overflow-hidden">
                    <div className="min-w-0 px-2.5 py-2 text-sm">
                      <div className={LINE1}>
                        <span
                          className={cn(
                            "dec inline-flex items-center gap-1 whitespace-nowrap text-xs",
                            DECISION_VIEW.held.tone,
                          )}
                        >
                          <Clock className="size-3.5 shrink-0" aria-hidden="true" />
                          <span>{t("held.waiting", "Waiting for your OK")}</span>
                        </span>
                        <span className={DEST}>{h.destination}</span>
                        <span className={TIME}>{fmtTime(h.created_at)}</span>
                      </div>
                      <span className="mt-1 block font-mono text-[11.5px] [overflow-wrap:anywhere]">
                        {h.text}
                      </span>
                      <div className={LINE3}>
                        {h.reason && <span className="text-foreground/85">{h.reason}</span>}
                        {inChat ? (
                          chatLink(h.session_id)
                        ) : (
                          <span>{t("held.from", "From {{caller}}", { caller: h.caller })}</span>
                        )}
                      </div>
                      <div className="mt-2 flex flex-wrap gap-1.5">
                        {inChat ? (
                          <Button size="sm" onClick={() => openChat(h.session_id as number, h.id)}>
                            <MessageSquareShare />
                            {t("held.openInChat", "Open in chat to decide")}
                          </Button>
                        ) : (
                          <>
                            <Button size="sm" disabled={busy.has(h.id)} onClick={() => void send(h)}>
                              {t("held.send", "Send")}
                            </Button>
                            <Button
                              size="sm"
                              variant="ghost"
                              disabled={busy.has(h.id)}
                              onClick={() => void discard(h)}
                            >
                              {t("held.discard", "Discard")}
                            </Button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </li>
              );
            })}
            {heldError && (
              <li role="alert" className={cn(ROW, "py-6 text-center text-muted-foreground", FADE_IN)}>
                <span className="mr-2">{t("loadFailed", "Couldn't load the log.")}</span>
                <Button size="sm" variant="outline" onClick={loadHeld}>
                  {t("retry", "Retry")}
                </Button>
              </li>
            )}
            {!heldError && heldRows.length === 0 && (
              <li className={cn(ROW, "py-6 text-center text-muted-foreground", FADE_IN)}>
                {t("held.empty", "No held searches.")}
              </li>
            )}
          </ul>
        </div>
      )}
    </div>
  );
}
