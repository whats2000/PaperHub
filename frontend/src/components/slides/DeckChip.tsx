import { ExternalLink, Download, Presentation } from "lucide-react";
import type { DeckEventData } from "@/types/domain";
import { useSlidesStore } from "@/store/slides";
import { deckPdfUrl } from "@/lib/api";
import { Button } from "@/components/ui/button";

interface Props {
  deck: DeckEventData;
  /**
   * Sends a chat message through the normal chat-send path. When provided (and
   * the deck is ready), the chip exposes Generate/Edit-notes + Edit-slide
   * affordances that simply SEND a turn — the backend's deck-command classifier
   * handles the rest. No new REST surface.
   */
  onSend?: (message: string) => void;
}

/**
 * DeckChip — compact card rendered below an assistant message when a slide
 * deck has been generated (deck SSE event). Shows title, page count, status,
 * and Open / Download actions.
 *
 * Styled to match SearchResultList rows: same card background, border, and
 * spacing.
 */
export function DeckChip({ deck, onSend }: Props) {
  const openPanel = useSlidesStore((s) => s.openPanel);
  const setCurrentPage = useSlidesStore((s) => s.setCurrentPage);

  const handleOpen = () => {
    openPanel();
    setCurrentPage(deck.session_id, 1);
  };

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
            <span>{deck.page_count} slide{deck.page_count !== 1 ? "s" : ""}</span>
            {/* Status indicator */}
            {deck.status === "ok" ? (
              <span className="text-green-600 dark:text-green-400">ready</span>
            ) : deck.status === "error" ? (
              <span className="text-destructive">error</span>
            ) : null}
            {deck.has_notes && (
              <span className="text-muted-foreground">with notes</span>
            )}
          </div>
        </div>

        {/* Actions */}
        <div className="flex items-center gap-1 shrink-0">
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
          <a
            href={deckPdfUrl(deck.session_id)}
            download
            aria-label="Download PDF"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-input bg-background text-sm font-medium transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <Download className="h-3 w-3" />
          </a>
          {deck.status === "ok" && onSend && (
            <>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                onClick={() =>
                  onSend(
                    deck.has_notes
                      ? "Edit the speaker notes for this deck"
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
                onClick={() => onSend("Edit this slide")}
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
