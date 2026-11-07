import { useRef, useState } from "react";
import { Check, ChevronRight, ThumbsDown, ThumbsUp } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Collapsible } from "@/components/ui/collapsible";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { usePresence } from "@/hooks/usePresence";
import { getRunFeedbackGuess, listFeedbackPresets } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useFeedbackStore } from "@/store/feedback";
import { REASONS, type FeedbackPut, type Preset } from "@/types/feedback";

const OTHER = "other";
// Mirrors the backend's FeedbackPut caps (feedback/models.py).
const OTHER_TEXT_MAX = 500;
const CORRECTION_MAX = 8000;
// Matches the popover's exit animation (duration-150).
const EXIT_MS = 150;

// Unpressed: a muted outline. Pressed: a solid thumb in the text colour.
const thumbClass =
  "h-6 w-6 text-muted-foreground transition-colors hover:text-foreground aria-pressed:text-foreground aria-pressed:[&_svg]:fill-current";

/**
 * Thumbs up / down for one completed answer. Up is a one-click toggle; down
 * opens a reasons popover pre-selected from the run's trace signals.
 */
export function FeedbackButtons({ runId }: { runId: number }) {
  const { t } = useTranslation("feedback");
  const vote = useFeedbackStore((s) => s.byRun[runId]);
  const submit = useFeedbackStore((s) => s.submit);

  const [open, setOpen] = useState(false);
  const presence = usePresence(open, EXIT_MS);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [guessed, setGuessed] = useState<string[]>([]);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [otherText, setOtherText] = useState("");
  const [correction, setCorrection] = useState("");
  const [correctionOpen, setCorrectionOpen] = useState(false);
  const touched = useRef(false);
  const openSeq = useRef(0);

  const save = (body: FeedbackPut) =>
    submit(runId, body).then(
      () => true,
      () => {
        toast.error(t("toast.failed"));
        return false;
      },
    );

  /** A second click on a pressed thumb removes the feedback. */
  const remove = () =>
    void save({ polarity: null, reasons: [] }).then((ok) => {
      if (ok) toast.success(t("toast.removed", "Feedback removed"));
    });

  const onUp = () => {
    if (vote?.polarity === "up") remove();
    else void save({ polarity: "up", reasons: [] });
  };

  const onOpenChange = (next: boolean) => {
    // A pressed thumbs down is removed on one click, like a pressed thumbs up.
    if (next && vote?.polarity === "down") {
      remove();
      return;
    }
    setOpen(next);
    if (!next) return;
    const seq = ++openSeq.current;
    touched.current = false;
    setSelected(new Set());
    setGuessed([]);
    setOtherText("");
    setCorrection("");
    setCorrectionOpen(false);
    getRunFeedbackGuess(runId).then(
      (g) => {
        if (seq !== openSeq.current) return;
        setGuessed(g.reasons);
        // Anything the user already picked wins over the guess.
        if (!touched.current) setSelected(new Set(g.reasons));
      },
      () => undefined, // No guess: the chips simply start empty.
    );
    listFeedbackPresets().then(
      (p) => {
        if (seq === openSeq.current) setPresets(p);
      },
      () => undefined,
    );
  };

  const toggle = (reason: string) => {
    touched.current = true;
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(reason)) next.delete(reason);
      else next.add(reason);
      return next;
    });
  };

  const options: { value: string; label: string }[] = [
    ...REASONS.map((r) => ({ value: r, label: t(`reason.${r}`) })),
    ...presets
      .filter((p) => !(REASONS as readonly string[]).includes(p.label))
      .map((p) => ({ value: p.label, label: p.label })),
    { value: OTHER, label: t("reason.other") },
  ];
  const otherOpen = selected.has(OTHER);

  const onSubmit = () => {
    // Display order first, then any selected value not shown yet (a saved
    // preset whose list hasn't loaded) so a quick re-submit can't drop it.
    const shown = options.map((o) => o.value);
    const reasons = [
      ...shown.filter((v) => selected.has(v)),
      ...[...selected].filter((v) => !shown.includes(v)),
    ];
    const body: FeedbackPut = { polarity: "down", reasons };
    // Rule: other_text is sent only while "Other reason" is selected; the
    // correction whenever it is non-empty, even if its expander is closed.
    if (otherOpen && otherText.trim()) body.other_text = otherText.trim();
    if (correction.trim()) body.correction = correction.trim();
    setOpen(false);
    void save(body).then((ok) => {
      if (ok) toast.success(t("toast.saved"));
    });
  };

  return (
    <>
      <Button
        type="button"
        size="icon"
        variant="ghost"
        className={thumbClass}
        aria-label={t("thumbs.up")}
        title={vote?.polarity === "up" ? t("thumbs.upPressed", "Marked as a good answer · click to remove") : t("thumbs.up")}
        aria-pressed={vote?.polarity === "up"}
        onClick={onUp}
      >
        <ThumbsUp className="h-3.5 w-3.5" />
      </Button>
      <Popover open={open} onOpenChange={onOpenChange}>
        <PopoverTrigger
          render={
            <Button
              type="button"
              size="icon"
              variant="ghost"
              className={thumbClass}
              aria-label={t("thumbs.down")}
              title={
                vote?.polarity === "down"
                  ? t("thumbs.downPressed", "Marked as a bad answer · click to remove")
                  : t("thumbs.down")
              }
              aria-pressed={vote?.polarity === "down"}
            />
          }
        >
          <ThumbsDown className="h-3.5 w-3.5" />
        </PopoverTrigger>
        <PopoverContent
          side="bottom"
          align="end"
          keepMounted={presence.mounted}
          className="w-[22.5rem] origin-(--transform-origin) duration-150 motion-reduce:animate-none"
        >
          <h5 className="mb-2 text-sm font-semibold">
            {t("popover.title")}{" "}
            <span className="text-xs font-normal text-muted-foreground">
              {t("popover.multi")}
            </span>
          </h5>
          <div className="flex flex-wrap gap-1.5">
            {options.map((o) => {
              const on = selected.has(o.value);
              return (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={on}
                  onClick={() => toggle(o.value)}
                  className={cn(
                    "inline-flex h-7 items-center gap-1 rounded-full border border-border px-2.5 text-xs transition-colors outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
                    on && "border-foreground bg-accent hover:bg-accent",
                  )}
                >
                  {on && <Check className="h-3 w-3" aria-hidden />}
                  {o.label}
                  {guessed.includes(o.value) && (
                    <span className="text-[10.5px] text-muted-foreground">
                      {t("popover.guess")}
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          <Collapsible open={otherOpen}>
            <div className="pt-2">
              <textarea
                rows={2}
                maxLength={OTHER_TEXT_MAX}
                value={otherText}
                onChange={(e) => setOtherText(e.target.value)}
                placeholder={t("popover.otherPlaceholder")}
                className="w-full resize-none rounded-md border border-input bg-transparent px-2 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              />
            </div>
          </Collapsible>
          <button
            type="button"
            aria-expanded={correctionOpen}
            onClick={() => setCorrectionOpen((v) => !v)}
            className="mt-2 inline-flex items-center gap-1 text-xs text-muted-foreground outline-none hover:text-foreground focus-visible:text-foreground"
          >
            <ChevronRight
              aria-hidden
              className={cn(
                "h-3 w-3 transition-transform duration-200 motion-reduce:transition-none",
                correctionOpen && "rotate-90",
              )}
            />
            {t("popover.correction")}
          </button>
          <Collapsible open={correctionOpen}>
            <div className="pt-1.5">
              <textarea
                rows={2}
                maxLength={CORRECTION_MAX}
                value={correction}
                onChange={(e) => setCorrection(e.target.value)}
                placeholder={t("popover.correctionPlaceholder")}
                className="w-full resize-none rounded-md border border-input bg-transparent px-2 py-1.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
              />
            </div>
          </Collapsible>
          <div className="mt-2.5 flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
              {t("popover.cancel")}
            </Button>
            <Button type="button" size="sm" onClick={onSubmit}>
              {t("popover.submit")}
            </Button>
          </div>
        </PopoverContent>
      </Popover>
    </>
  );
}
