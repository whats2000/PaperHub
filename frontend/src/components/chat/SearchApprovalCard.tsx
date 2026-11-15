import { useEffect, useRef, useState } from "react";
import { Check, CircleSlash, Globe, Loader2, Pencil, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { allowSearch, dismissSearch, getAction, type AlsoAllowed } from "@/lib/api";
import { prefersReducedMotion } from "@/lib/motion";
import { useDestinationLabel } from "@/lib/outboundDestination";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import type { ConfirmRequestData, SearchDecision } from "@/types/domain";

// Matches duration-200 plus a margin: finish even if no transitionend fires.
const EXIT_FALLBACK_MS = 250;
// The jump ring holds this long, then fades over its 1 s transition.
const FLASH_HOLD_MS = 400;
/** The backend's cap on "Don't allow"'s note. */
export const NOTE_MAX_CHARS = 1000;

type Patch = Partial<ConfirmRequestData>;

/** The HTTP status and body of an `apiFetch` error ("API 422: {...}"). */
function parseApiError(e: unknown): { status: number | null; body: string } {
  const msg = e instanceof Error ? e.message : "";
  const m = /^API (\d{3}): ?([\s\S]*)$/.exec(msg);
  return m ? { status: Number(m[1]), body: m[2] ?? "" } : { status: null, body: msg };
}

function detailOf(body: string): { detail?: string; term?: string } {
  try {
    return JSON.parse(body) as { detail?: string; term?: string };
  } catch {
    return {};
  }
}

function Destination({ host }: { host: string }) {
  return <>{useDestinationLabel(host)}</>;
}

/**
 * Ask first's "Allow this search?" card (H5a): the agent's `search_papers`
 * call waits here before anything leaves. It shows the query, where it would
 * go and why the user is asked, with Allow / Allow searches in this chat /
 * Edit, then allow / Don't allow (which opens an optional note for the
 * agent, growing down from the button). Deciding collapses the card
 * (grid-rows, 200 ms) into a muted line; the paused turn then searches (or
 * not) and continues. A jump from the "needs your OK" pill rings it.
 */
export function SearchApprovalCard({
  data,
  onChange,
  waitingSiblings = 0,
  onAlsoAllowed,
}: {
  data: ConfirmRequestData;
  /** Persist a status/result change on the message's event. */
  onChange: (patch: Patch) => void;
  /** Other search cards of this turn still waiting: "Allow searches in this
   *  chat" allows them too, so its label says how many. */
  waitingSiblings?: number;
  /** The other cards the chat-wide allow settled (the server's list). */
  onAlsoAllowed?: (items: AlsoAllowed[]) => void;
}) {
  const { t } = useTranslation("chat");
  const target = data.target?.kind === "search" ? data.target : null;
  const query = target?.query ?? data.args_summary;
  const destinations = target?.destinations ?? [];
  const status = data.status ?? "pending";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(query);
  const [denyOpen, setDenyOpen] = useState(false);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState<"allow" | "chat" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const finished = useRef(false);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const denyRef = useRef<HTMLDivElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const focusId = useNavStore((s) => s.focusHeldId);
  const pending = status === "pending";

  useEffect(
    () => () => {
      if (exitTimer.current !== null) clearTimeout(exitTimer.current);
      if (flashTimer.current !== null) clearTimeout(flashTimer.current);
    },
    [],
  );
  useEffect(() => {
    if (editing) taRef.current?.focus();
  }, [editing]);
  // The note box grows open, then takes focus and scrolls into view.
  useEffect(() => {
    if (!denyOpen) return;
    const reduce = prefersReducedMotion();
    const timer = setTimeout(
      () => {
        noteRef.current?.focus({ preventScroll: true });
        denyRef.current?.scrollIntoView?.({
          block: "nearest",
          behavior: reduce ? "auto" : "smooth",
        });
      },
      reduce ? 0 : 200,
    );
    return () => clearTimeout(timer);
  }, [denyOpen]);

  // A jump to this card ("needs your OK" pill): centre it, ring it briefly
  // and focus Allow. Consumed once.
  useEffect(() => {
    const group = groupRef.current;
    if (focusId !== data.action_id || !pending || !group) return;
    const jump = setTimeout(() => {
      useNavStore.getState().setFocusHeldId(null);
      const reduce = prefersReducedMotion();
      group.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      primaryRef.current?.focus({ preventScroll: true });
      if (reduce) return;
      group.dataset.flash = "true";
      if (flashTimer.current !== null) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => {
        flashTimer.current = null;
        delete group.dataset.flash;
      }, FLASH_HOLD_MS);
    }, 0);
    return () => clearTimeout(jump);
  }, [focusId, data.action_id, pending]);

  const finishExit = () => {
    if (finished.current) return;
    finished.current = true;
    if (exitTimer.current !== null) clearTimeout(exitTimer.current);
    setClosing(false);
  };

  // The decision is stored at once; the timer only drives the collapse.
  const collapseInto = (patch: Patch) => {
    onChange(patch);
    useNavStore.getState().refreshHeld();
    finished.current = false;
    setClosing(true);
    if (prefersReducedMotion()) finishExit();
    else exitTimer.current = setTimeout(finishExit, EXIT_FALLBACK_MS);
  };

  // Settled from outside — another card's "Allow searches in this chat"
  // allowed this one too: collapse the same way a decision made here does.
  const [seenPending, setSeenPending] = useState(pending);
  if (seenPending !== pending) {
    setSeenPending(pending);
    if (seenPending && !pending && !closing) setClosing(true);
  }
  // Ends any collapse (the transition's end usually comes first).
  useEffect(() => {
    if (!closing) return;
    const timer = setTimeout(finishExit, prefersReducedMotion() ? 0 : EXIT_FALLBACK_MS);
    return () => clearTimeout(timer);
  }, [closing]);

  // 409: decided elsewhere (another tab) or the turn is gone. Show its state.
  const handleStale = async (body: string) => {
    if (detailOf(body).detail === "turn_ended") {
      collapseInto({ status: "dismissed", result: { error: "turn_ended" } });
      return;
    }
    toast.error(t("outbound.ask.stale", "This search was already decided."));
    try {
      const state = await getAction(data.action_id);
      collapseInto({
        status: state.status === "pending" ? "unavailable" : state.status,
        result: state.result ?? null,
      });
    } catch {
      collapseInto({ status: "unavailable" });
    }
  };

  const allow = async (allowChat: boolean) => {
    if (inFlight.current) return;
    const text = editing ? draft.trim() : query;
    if (!text) {
      setError(t("outbound.held.empty", "There is nothing to send."));
      return;
    }
    inFlight.current = true;
    setBusy(allowChat ? "chat" : "allow");
    setError(null);
    try {
      const res = await allowSearch(data.action_id, {
        text: editing && text !== query ? text : undefined,
        allowChat,
      });
      if (res.allow_chat) {
        // The card lives in the open chat.
        const local = useChatStore.getState().activeSessionId;
        if (local !== null) useChatStore.getState().setAllowSearches(local, true);
        toast(
          t(
            "outbound.ask.chatAllowed",
            "Searches in this chat now run without asking. Actions still ask.",
          ),
        );
      }
      collapseInto({
        status: "running",
        result: {
          decision: "allowed",
          text: res.text,
          edited: res.edited,
          allow_chat: res.allow_chat,
        },
      });
      if ((res.also_allowed ?? []).length > 0) onAlsoAllowed?.(res.also_allowed ?? []);
    } catch (e) {
      const { status: code, body } = parseApiError(e);
      const detail = detailOf(body);
      if (code === 422 && detail.detail === "sensitive_term") {
        setError(
          detail.term
            ? t(
                "outbound.held.sensitive",
                "It contains a sensitive term ({{term}}), so it can't be sent. Edit it and try again.",
                { term: detail.term },
              )
            : t(
                "outbound.held.sensitiveNoTerm",
                "It contains a sensitive term, so it can't be sent. Edit it and try again.",
              ),
        );
      } else if (code === 422) {
        setError(t("outbound.held.empty", "There is nothing to send."));
      } else if (code === 409) {
        await handleStale(body);
      } else {
        toast.error(t("outbound.ask.failed", "Couldn't reach the server. Try again."));
      }
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const deny = async () => {
    if (inFlight.current) return;
    const text = note.trim();
    if (text.length > NOTE_MAX_CHARS) {
      setError(t("outbound.ask.noteTooLong", "The note is too long (at most 1,000 characters)."));
      return;
    }
    inFlight.current = true;
    setBusy("deny");
    setError(null);
    try {
      await dismissSearch(data.action_id, text || undefined);
      collapseInto({ status: "dismissed", result: { decision: "declined", note: text || null } });
    } catch (e) {
      const { status: code, body } = parseApiError(e);
      if (code === 409) await handleStale(body);
      else toast.error(t("outbound.ask.failed", "Couldn't reach the server. Try again."));
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const lineClass =
    "not-prose mt-2 flex origin-top-left flex-wrap items-center gap-1.5 text-xs text-muted-foreground animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none";

  if (!pending && !closing) {
    const result = (data.result ?? {}) as SearchDecision;
    const ended = result.error === "turn_ended" || data.error === "turn_ended";
    // Allowed: the search ran (or failed on its own; the trace says why).
    if (!ended && (status === "running" || status === "done" || status === "failed")) {
      const text = result.text ?? query;
      return (
        <p data-testid="ask-line" data-state="allowed" className={lineClass}>
          <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {result.edited
              ? t("outbound.ask.allowedEdited", "Allowed after your edit")
              : t("outbound.ask.allowed", "Allowed")}
            : <span className="font-mono text-[11.5px] text-foreground">{text}</span>
            {result.by_chat_setting ? (
              <> · {t("outbound.ask.allowedBySetting", "searches are allowed in this chat")}</>
            ) : (
              result.allow_chat && (
                <> · {t("outbound.ask.allowedChat", "later searches in this chat won't ask")}</>
              )
            )}
          </span>
        </p>
      );
    }
    let line: string;
    let noteText: string | null = null;
    if (status === "unavailable") {
      line = t("outbound.ask.unavailable", "This search is no longer available.");
    } else if (ended) {
      line = t("outbound.ask.ended", "Not searched. This turn ended before you decided.");
    } else if (result.decision === "declined" && result.note) {
      line = t("outbound.ask.declinedNote", "Not searched. You told the agent:");
      noteText = result.note;
    } else if (result.decision === "declined") {
      line = t("outbound.ask.declined", "Not searched. You didn't allow this search.");
    } else {
      line = t("outbound.ask.stopped", "Not searched. The turn was stopped.");
    }
    return (
      <p data-testid="ask-line" data-state="declined" className={lineClass}>
        <CircleSlash className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>
          {line}
          {noteText && <> “{noteText}”</>}
        </span>
      </p>
    );
  }

  const disabled = busy !== null || closing;
  return (
    <div
      data-testid="ask-card"
      data-state={closing ? "closed" : "open"}
      onTransitionEnd={(e) => {
        if (closing && e.target === e.currentTarget && e.propertyName === "grid-template-rows") {
          finishExit();
        }
      }}
      className="grid grid-rows-[1fr] opacity-100 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none"
    >
      <div className="min-h-0 overflow-hidden">
        <div
          ref={groupRef}
          data-action-id={data.action_id}
          role="group"
          aria-label={t("outbound.ask.title", "Allow this search?")}
          // Inset: the collapse wrapper's overflow-hidden would clip an outer ring.
          className="not-prose mt-2 origin-top-left rounded-lg border border-border bg-card p-3 text-sm shadow-sm ring-0 ring-amber-300 ring-inset transition-[box-shadow] ease-out [transition-duration:1s] animate-in fade-in-0 zoom-in-95 animation-duration-200 motion-reduce:animate-none motion-reduce:transition-none data-[flash=true]:ring-[3px] data-[flash=true]:[transition-duration:0s] dark:ring-amber-700"
        >
          <div className="flex items-center gap-2 font-medium">
            <Globe className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            {t("outbound.ask.title", "Allow this search?")}
          </div>
          <textarea
            ref={taRef}
            aria-label={t("outbound.ask.textLabel", "Search query")}
            readOnly={!editing}
            spellCheck={false}
            rows={1}
            value={editing ? draft : query}
            onChange={(e) => {
              setDraft(e.target.value);
              setError(null);
            }}
            className={`mt-2 block w-full resize-none rounded-md border border-border px-2 py-1.5 font-mono text-xs [field-sizing:content] transition-colors duration-200 [overflow-wrap:anywhere] focus:outline-none motion-reduce:transition-none ${
              editing ? "bg-background ring-2 ring-ring" : "bg-muted"
            }`}
          />
          {error && (
            <p
              role="alert"
              className="mt-1 text-xs text-destructive animate-in fade-in-0 duration-200 motion-reduce:animate-none"
            >
              {error}
            </p>
          )}
          <p className="mt-1.5 text-xs text-muted-foreground">
            <b className="font-medium text-foreground">{t("outbound.held.to", "To:")}</b>{" "}
            {destinations.length > 0
              ? destinations.map((d, i) => (
                  <span key={d}>
                    {i > 0 && ", "}
                    <Destination host={d} />
                  </span>
                ))
              : t("outbound.ask.noDestination", "no search service is allowed")}
          </p>
          <p className="mt-1.5 text-xs text-muted-foreground">
            <b className="font-medium text-foreground">
              {t("outbound.held.why", "Why you're asked:")}
            </b>{" "}
            {t("outbound.ask.whyText", "Ask first is on for this chat. Nothing has been sent.")}
          </p>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            {t(
              "outbound.ask.followUps",
              "The search may also send follow-up queries it writes from this one. Each is still checked for your sensitive terms and private material.",
            )}
          </p>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            <Button
              ref={primaryRef}
              type="button"
              size="sm"
              disabled={disabled}
              onClick={() => void allow(false)}
              className="h-7 gap-1.5 px-2.5 text-xs"
            >
              {busy === "allow" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Check className="h-3.5 w-3.5" aria-hidden />
              )}
              {editing
                ? t("outbound.ask.allowEdited", "Allow edited search")
                : t("outbound.ask.allow", "Allow")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled}
              aria-label={
                waitingSiblings > 0
                  ? `${t("outbound.ask.allowChat", "Allow searches in this chat")} · ${t(
                      "outbound.ask.alsoAllows",
                      "also allows {{count}} waiting",
                      { count: waitingSiblings },
                    )}`
                  : undefined
              }
              onClick={() => void allow(true)}
              className="h-7 gap-1.5 px-2.5 text-xs"
            >
              {busy === "chat" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {t("outbound.ask.allowChat", "Allow searches in this chat")}
              {waitingSiblings > 0 && (
                <span className="text-muted-foreground">
                  {"· "}
                  {t("outbound.ask.alsoAllows", "also allows {{count}} waiting", {
                    count: waitingSiblings,
                  })}
                </span>
              )}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => {
                setDraft(query);
                setError(null);
                setEditing((v) => !v);
              }}
              className="h-7 gap-1.5 px-2.5 text-xs"
            >
              {editing ? (
                <X className="h-3.5 w-3.5" aria-hidden />
              ) : (
                <Pencil className="h-3.5 w-3.5" aria-hidden />
              )}
              {editing
                ? t("outbound.held.cancelEdit", "Cancel edit")
                : t("outbound.ask.edit", "Edit, then allow")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={disabled}
              aria-expanded={denyOpen}
              aria-controls={`ask-deny-${data.action_id}`}
              onClick={() => setDenyOpen((v) => !v)}
              className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground"
            >
              {t("outbound.ask.deny", "Don't allow")}
            </Button>
          </div>
          {/* The note box grows down from the buttons and folds back into them. */}
          <div
            id={`ask-deny-${data.action_id}`}
            ref={denyRef}
            data-testid="ask-deny"
            data-state={denyOpen ? "open" : "closed"}
            inert={!denyOpen}
            className="grid grid-rows-[0fr] opacity-0 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=open]:grid-rows-[1fr] data-[state=open]:opacity-100 motion-reduce:transition-none"
          >
            <div className="min-h-0 overflow-hidden">
              <label
                htmlFor={`ask-note-${data.action_id}`}
                className="mt-2.5 block text-xs text-muted-foreground"
              >
                {t("outbound.ask.denyLabel", "Tell the agent what to do instead (optional)")}
              </label>
              <textarea
                id={`ask-note-${data.action_id}`}
                ref={noteRef}
                rows={2}
                maxLength={NOTE_MAX_CHARS}
                value={note}
                placeholder={t(
                  "outbound.ask.denyPlaceholder",
                  "e.g. Only use papers already in this chat.",
                )}
                onChange={(e) => {
                  setNote(e.target.value);
                  setError(null);
                }}
                className="mt-1 block w-full resize-y rounded-md border border-border bg-background px-2 py-1.5 text-xs focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
              <div className="mt-1.5 flex gap-1.5">
                <Button
                  type="button"
                  size="sm"
                  disabled={disabled}
                  onClick={() => void deny()}
                  className="h-7 gap-1.5 px-2.5 text-xs"
                >
                  {busy === "deny" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
                  {t("outbound.ask.deny", "Don't allow")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={disabled}
                  onClick={() => setDenyOpen(false)}
                  className="h-7 px-2.5 text-xs"
                >
                  {t("outbound.ask.cancel", "Cancel")}
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
