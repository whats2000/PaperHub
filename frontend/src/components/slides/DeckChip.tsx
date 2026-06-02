import { useCallback, useState } from "react";
import {
  ExternalLink,
  Download,
  History,
  Loader2,
  Presentation,
} from "lucide-react";
import { toast } from "sonner";

import type { DeckEventData } from "@/types/domain";
import { useSlidesStore } from "@/store/slides";
import { API_BASE_URL, deckPdfUrl, getDeck } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface Props {
  deck: DeckEventData;
  /**
   * Prefill the composer with an editable prompt (does NOT send). When provided
   * (and the deck is ready), the chip exposes Generate/Edit-notes + Edit-slide
   * affordances that drop a starter prompt into the input so the user can say
   * WHAT to change before sending — a bare "Edit this slide" is useless.
   */
  onPrefill?: (message: string) => void;
}

/**
 * DeckChip — compact card rendered below an assistant message when a slide
 * deck has been generated (deck SSE event). Shows title, page count, status,
 * and Open / Download / Switch-version actions.
 *
 * F4.5: each generate / edit turn surfaces its OWN card (the run carries a
 * `deck_version_id`). The card knows which version it stamped; the SlidesPanel
 * separately knows the currently-active deck (from `useSlidesStore`). When the
 * card's version IS the active one the affordance reads "Open"; otherwise it
 * reads "Switch to this version" → POSTs `/deck/versions/{vid}/restore` and
 * republishes the deck event so `useSlidesStore.setDeck` bumps the revision,
 * which the SlidesPanel watches to refetch the PDF bytes.
 *
 * Styled to match SearchResultList rows: same card background, border, and
 * spacing.
 */
export function DeckChip({ deck, onPrefill }: Props) {
  const openPanel = useSlidesStore((s) => s.openPanel);
  const setCurrentPage = useSlidesStore((s) => s.setCurrentPage);
  const setDeck = useSlidesStore((s) => s.setDeck);
  // Per-session "restore in flight" flag. We flip it true around the POST +
  // getDeck round-trip so the SlidesPanel mask covers the OLD-pdf-still-on-
  // screen window (mirrors how a chat-turn edit's ``busy`` prop masks).
  const setStoreRestoring = useSlidesStore((s) => s.setRestoring);
  // The session's currently-active deck (latest deck SSE event or restore).
  // We compare this card's version_id against the active one to decide
  // whether to show "Open" vs "Switch to this version".
  const activeDeck = useSlidesStore(
    (s) => s.deckBySession[deck.session_id],
  );

  const [restoring, setRestoring] = useState(false);

  // A card is the "active" version when its version_id matches the active
  // deck's version_id. Legacy cards (no version_id) fall back to "active
  // unless the active deck has a different deck_id" — pre-F4.5 sessions
  // only ever produced one card so the legacy branch must default to
  // active when no active deck is tracked yet (also keeps unit tests that
  // don't seed the slides store from regressing on the chip's affordances).
  const isActiveVersion = deck.version_id
    ? deck.version_id === activeDeck?.version_id
    : activeDeck === undefined || activeDeck.deck_id === deck.deck_id;

  const handleOpen = useCallback(() => {
    openPanel();
    setCurrentPage(deck.session_id, 1);
  }, [openPanel, setCurrentPage, deck.session_id]);

  const handleSwitch = useCallback(async () => {
    if (!deck.version_id || restoring) return;
    setRestoring(true);
    setStoreRestoring(deck.session_id, true);
    try {
      const res = await fetch(
        `${API_BASE_URL}/sessions/${deck.session_id}/deck/versions/${deck.version_id}/restore`,
        {
          method: "POST",
          headers: {
            "X-Paperhub-Session-Id": String(deck.session_id),
          },
        },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      // Pull the freshly-restored deck row + republish so the SlidesPanel
      // sees a NEW deckBySession entry → revision bump → PDF refetch. We
      // build a DeckEventData out of the meta payload (same shape the SSE
      // `deck` event produces) so the panel's existing fetch effect fires.
      const meta = await getDeck(deck.session_id);
      const event: DeckEventData = {
        deck_id: meta.deck_id,
        session_id: meta.session_id,
        page_count: meta.page_count,
        title:
          (meta.plan as { title?: string } | null)?.title ??
          deck.title ??
          "Slides",
        status: meta.status,
        contributing_papers: [],
        has_notes: Object.keys(meta.speaker_notes).length > 0,
        version_id: meta.current_version_id ?? deck.version_id,
      };
      setDeck(deck.session_id, event);
      // Reset to page 1 on a version switch — the previous page may not
      // exist in the restored deck (frame count can change between edits).
      setCurrentPage(deck.session_id, 1);
      openPanel();
    } catch (err) {
      console.error("Failed to restore deck version", err);
      toast.error("Failed to restore version");
    } finally {
      setRestoring(false);
      setStoreRestoring(deck.session_id, false);
    }
  }, [
    deck.session_id,
    deck.version_id,
    deck.title,
    restoring,
    setDeck,
    setCurrentPage,
    setStoreRestoring,
    openPanel,
  ]);

  return (
    <div className="mt-2 rounded-xl border border-border bg-card px-3 py-2.5 text-sm shadow-sm">
      <div className="flex items-start gap-2">
        {/* Icon */}
        <Presentation className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />

        {/* Content */}
        <div className="flex-1 min-w-0">
          <p className="font-medium leading-snug truncate" title={deck.title}>
            {deck.title}
          </p>
          <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
            <span>
              {deck.page_count} slide{deck.page_count !== 1 ? "s" : ""}
            </span>
            {/* Status indicator */}
            {deck.status === "ok" ? (
              <span className="text-green-600 dark:text-green-400">ready</span>
            ) : deck.status === "error" ? (
              <span className="text-destructive">error</span>
            ) : null}
            {deck.has_notes && (
              <span className="text-muted-foreground">with notes</span>
            )}
            {isActiveVersion && (
              <span
                className="text-primary"
                title="This is the version currently open in the Slides panel"
              >
                active
              </span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
          {isActiveVersion ? (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleOpen}
              className="h-7 px-2 text-xs gap-1"
              aria-label="Open slides"
            >
              <ExternalLink className="h-3 w-3" />
              Open
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void handleSwitch()}
              disabled={!deck.version_id || restoring}
              className="h-7 px-2 text-xs gap-1"
              aria-label="Switch to this version"
              title={
                deck.version_id
                  ? "Restore this deck version + reload the Slides panel"
                  : "This older card pre-dates per-turn version tracking"
              }
            >
              {restoring ? (
                <Loader2 className="h-3 w-3 animate-spin" />
              ) : (
                <History className="h-3 w-3" />
              )}
              Switch
            </Button>
          )}
          <a
            href={deckPdfUrl(deck.session_id)}
            download
            aria-label="Download PDF"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-input bg-background text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Download className="h-3 w-3" />
          </a>
          {deck.status === "ok" && onPrefill && isActiveVersion && (
            <>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                onClick={() =>
                  onPrefill(
                    deck.has_notes
                      ? "Edit the speaker notes for this deck: "
                      : "Generate speaker notes for this deck",
                  )
                }
                aria-label={deck.has_notes ? "Edit notes" : "Generate notes"}
              >
                {deck.has_notes ? "Edit notes" : "Generate notes"}
              </Button>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                onClick={() => onPrefill("Edit this slide: ")}
                aria-label="Edit slide"
              >
                Edit
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
