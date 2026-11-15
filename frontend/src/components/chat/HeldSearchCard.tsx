import { useEffect, useRef, useState } from "react";
import { CircleSlash, Loader2, Pencil, Send, ShieldQuestion, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { discardHeld, sendHeld } from "@/lib/api";
import { prefersReducedMotion } from "@/lib/motion";
import { useDestinationLabel } from "@/lib/outboundDestination";
import { useNavStore } from "@/store/nav";
import type { HeldStatus, OutboundHeldData } from "@/types/domain";

// Matches duration-200 plus a margin: finish even if no transitionend fires.
const EXIT_FALLBACK_MS = 250;
// The jump ring holds this long, then fades over its 1 s transition.
const FLASH_HOLD_MS = 400;

/** The HTTP status and body of an `apiFetch` error ("API 422: {...}"). */
function parseApiError(e: unknown): { status: number | null; body: string } {
  const msg = e instanceof Error ? e.message : "";
  const m = /^API (\d{3}): ?([\s\S]*)$/.exec(msg);
  return m ? { status: Number(m[1]), body: m[2] ?? "" } : { status: null, body: msg };
}

function sensitiveTerm(body: string): string | null {
  try {
    const j = JSON.parse(body) as { detail?: string; term?: string };
    return j.detail === "sensitive_term" ? (j.term ?? "") : null;
  } catch {
    return null;
  }
}

type Patch = Partial<OutboundHeldData>;

/**
 * A search the outbound checkpoint held for the user's OK (H5a): the exact
 * text, why they are asked and where it would go, with Send as shown / Edit,
 * then send / Don't send. Deciding collapses the card (grid-rows, 200 ms)
 * into a muted line. A live turn paused on the card gets the decision and
 * runs the search itself (`resumed`); with no live turn waiting, sending
 * starts the follow-up turn that runs it. A card replayed as `sent` whose
 * follow-up never ran offers "Run the search".
 */
export function HeldSearchCard({
  data,
  followUpRan,
  onChange,
  onRun,
}: {
  data: OutboundHeldData;
  /** A "Sent search: <text>" turn already exists in this chat. */
  followUpRan: boolean;
  /** Persist a status change on the message's event. */
  onChange: (patch: Patch) => void;
  /** Start the follow-up turn for this approved search; rejects on a 409. */
  onRun: (heldId: string, text: string) => Promise<void>;
}) {
  const { t } = useTranslation("chat");
  const dest = useDestinationLabel(data.destination);
  const status: HeldStatus = data.status ?? "pending";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(data.text);
  const [busy, setBusy] = useState<"send" | "discard" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [closing, setClosing] = useState(false);
  const finished = useRef(false);
  const exitTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inFlight = useRef(false);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const groupRef = useRef<HTMLDivElement>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const focusHeldId = useNavStore((s) => s.focusHeldId);

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

  // A jump to this card (sidebar Held tab / "needs your OK" pill): scroll it
  // to the centre, ring it briefly and focus Send. Consumed once.
  const pending = status === "pending";
  useEffect(() => {
    const group = groupRef.current;
    if (focusHeldId !== data.held_id || !pending || !group) return;
    // Next task: the thread's own scroll-to-bottom (a parent effect, run
    // after this one when the chat's messages just loaded) must not win.
    const jump = setTimeout(() => {
      useNavStore.getState().setFocusHeldId(null);
      const reduce = prefersReducedMotion();
      group.scrollIntoView({ block: "center", behavior: reduce ? "auto" : "smooth" });
      primaryRef.current?.focus({ preventScroll: true });
      if (reduce) return;
      // The ring holds briefly, then fades over its 1 s transition (~1.4 s total).
      group.dataset.flash = "true";
      if (flashTimer.current !== null) clearTimeout(flashTimer.current);
      flashTimer.current = setTimeout(() => {
        flashTimer.current = null;
        delete group.dataset.flash;
      }, FLASH_HOLD_MS);
    }, 0);
    return () => clearTimeout(jump);
  }, [focusHeldId, data.held_id, pending]);

  const finishExit = () => {
    if (finished.current) return;
    finished.current = true;
    if (exitTimer.current !== null) clearTimeout(exitTimer.current);
    setClosing(false);
  };

  // The decision is stored at once (the card may unmount before the exit
  // timer fires); the timer only drives the collapse animation.
  const collapseInto = (patch: Patch) => {
    onChange(patch);
    // The sidebar's held counts follow the decision.
    useNavStore.getState().refreshHeld();
    finished.current = false;
    setClosing(true);
    if (prefersReducedMotion()) finishExit();
    else exitTimer.current = setTimeout(finishExit, EXIT_FALLBACK_MS);
  };

  // Run the follow-up turn; a 409 means it can no longer run, so the card
  // settles on plain "Sent".
  const run = (text: string) => {
    onRun(data.held_id, text).catch((e: unknown) => {
      const msg = e instanceof Error ? e.message : String(e);
      if (/\b409\b/.test(msg)) {
        toast.error(t("outbound.held.alreadyRun", "This search was already run."));
        onChange({ settled: true });
      } else {
        toast.error(t("outbound.held.runFailed", "Couldn't start the search. Try again."));
      }
    });
  };

  const handleStale = (body: string) => {
    toast.error(t("outbound.held.stale", "This search was already handled."));
    if (/is sent/.test(body)) collapseInto({ status: "sent", settled: true });
    else if (/is discarded/.test(body)) collapseInto({ status: "discarded" });
    else collapseInto({ status: "unavailable" });
  };

  const send = async () => {
    if (inFlight.current) return;
    const text = editing ? draft.trim() : data.text;
    if (!text) {
      setError(t("outbound.held.empty", "There is nothing to send."));
      return;
    }
    inFlight.current = true;
    setBusy("send");
    setError(null);
    try {
      const res = await sendHeld(data.held_id, editing && text !== data.text ? text : undefined);
      if (res.resumed) {
        // The paused turn sends it and continues: no follow-up turn.
        collapseInto({ status: "sent", sent_text: res.text, ran: true });
      } else {
        collapseInto({ status: "sent", sent_text: res.text });
        run(res.text);
      }
    } catch (e) {
      const { status: code, body } = parseApiError(e);
      const term = code === 422 ? sensitiveTerm(body) : null;
      if (term !== null) {
        setError(
          term
            ? t(
                "outbound.held.sensitive",
                "It contains a sensitive term ({{term}}), so it can't be sent. Edit it and try again.",
                { term },
              )
            : t(
                "outbound.held.sensitiveNoTerm",
                "It contains a sensitive term, so it can't be sent. Edit it and try again.",
              ),
        );
      } else if (code === 422 && body.includes("empty_text")) {
        setError(t("outbound.held.empty", "There is nothing to send."));
      } else if (code === 409) {
        handleStale(body);
      } else {
        toast.error(t("outbound.held.failed", "Couldn't send it. Try again."));
      }
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const discard = async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy("discard");
    try {
      await discardHeld(data.held_id);
      collapseInto({ status: "discarded" });
    } catch (e) {
      const { status: code, body } = parseApiError(e);
      if (code === 409) handleStale(body);
      else toast.error(t("outbound.held.discardFailed", "Couldn't discard it. Try again."));
    } finally {
      inFlight.current = false;
      setBusy(null);
    }
  };

  const lineClass =
    "not-prose mt-2 flex origin-top-left flex-wrap items-center gap-1.5 text-xs text-muted-foreground animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none";
  const sentText = data.sent_text ?? data.text;

  if (status !== "pending" && !closing) {
    if (status === "discarded") {
      return (
        <p data-testid="held-line" data-state="discarded" className={lineClass}>
          <CircleSlash className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("outbound.held.discarded", "Not sent. You chose not to send this search.")}</span>
        </p>
      );
    }
    if (status === "unavailable") {
      return (
        <p data-testid="held-line" data-state="unavailable" className={lineClass}>
          <CircleSlash className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{t("outbound.held.unavailable", "This search is no longer available.")}</span>
        </p>
      );
    }
    if (!followUpRan && !data.settled && !data.ran) {
      return (
        <p data-testid="held-line" data-state="approved" className={lineClass}>
          <Send className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>
            {t("outbound.held.approvedNotRun", "Approved, not run yet")}:{" "}
            <span className="font-mono text-[11.5px] text-foreground">{sentText}</span>
          </span>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 gap-1 px-2 text-xs"
            onClick={() => run(sentText)}
          >
            {t("outbound.held.run", "Run the search")}
          </Button>
        </p>
      );
    }
    const edited = data.sent_text !== undefined && data.sent_text !== data.text;
    return (
      <p data-testid="held-line" data-state="sent" className={lineClass}>
        <Send className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <span>
          {edited
            ? t("outbound.held.sentEdited", "Sent after your edit")
            : t("outbound.held.sent", "Sent")}
          : <span className="font-mono text-[11.5px] text-foreground">{sentText}</span>
        </span>
      </p>
    );
  }

  const disabled = busy !== null || closing;
  return (
    <div
      data-testid="held-card"
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
          data-held-id={data.held_id}
          role="group"
          aria-label={t("outbound.held.title", "This search needs your OK")}
          // Inset: the collapse wrapper's overflow-hidden would clip an outer ring.
          className="not-prose mt-2 origin-top-left rounded-lg border border-border bg-card p-3 text-sm shadow-sm ring-0 ring-amber-300 ring-inset transition-[box-shadow] ease-out [transition-duration:1s] animate-in fade-in-0 zoom-in-95 animation-duration-200 motion-reduce:animate-none motion-reduce:transition-none data-[flash=true]:ring-[3px] data-[flash=true]:[transition-duration:0s] dark:ring-amber-700"
        >
          <div className="flex items-center gap-2 font-medium">
            <ShieldQuestion className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
            {t("outbound.held.title", "This search needs your OK")}
          </div>
          <textarea
            ref={taRef}
            aria-label={t("outbound.held.textLabel", "Search text")}
            readOnly={!editing}
            spellCheck={false}
            rows={1}
            value={editing ? draft : data.text}
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
            <b className="font-medium text-foreground">{t("outbound.held.why", "Why you're asked:")}</b>{" "}
            {data.reason}
          </p>
          <p className="mt-1.5 text-xs text-muted-foreground">
            <b className="font-medium text-foreground">{t("outbound.held.to", "To:")}</b> {dest}
          </p>
          <div className="mt-2.5 flex flex-wrap gap-1.5">
            <Button
              ref={primaryRef}
              type="button"
              size="sm"
              disabled={disabled}
              onClick={() => void send()}
              className="h-7 gap-1.5 px-2.5 text-xs"
            >
              {busy === "send" ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              ) : (
                <Send className="h-3.5 w-3.5" aria-hidden />
              )}
              {editing
                ? t("outbound.held.sendEdited", "Send edited text")
                : t("outbound.held.sendAsShown", "Send as shown")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              disabled={disabled}
              onClick={() => {
                setDraft(data.text);
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
                : t("outbound.held.edit", "Edit, then send")}
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={disabled}
              onClick={() => void discard()}
              className="h-7 gap-1.5 px-2.5 text-xs text-muted-foreground"
            >
              {busy === "discard" && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />}
              {t("outbound.held.discard", "Don't send")}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}
