import { useRef, useState } from "react";
import { toast } from "sonner";

import type {
  ReferenceItem,
  SearchResultCandidate,
} from "@/types/domain";
import { ingestPaper, listSessionReferences, uploadPdf } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

/**
 * Normalise a paper title for fuzzy-free exact matching across sources that
 * may differ in casing or whitespace (the ingest pipeline replaces the
 * Semantic Scholar guessed title with the authoritative source title, so
 * they will be equal modulo casing/spacing in the common case).
 */
function normalizeTitle(t: string): string {
  return t.toLowerCase().replace(/\s+/g, " ").trim();
}

/**
 * Find the reference row (if any) that corresponds to a search candidate.
 *
 * Match order:
 *   1. `papers_id` — fastest, exact numeric identity.
 *   2. `arxiv_id` — bridges identity before papers_id is known on the candidate.
 *   3. Normalised title — catches a PDF uploaded via the composer paperclip
 *      (refs land with arxiv_id=null; title is the authoritative join key after
 *      ingest replaces the guessed title with the source title).
 *
 * Returns ``undefined`` when nothing matches — the card should then render
 * as an "Add as reference" action. This is what makes the chat-side state
 * track the panel-side state: when the user removes a paper from the
 * References panel, the ref disappears, the match drops, the card flips
 * back to the Add button.
 */
function findMatchingRef(
  candidate: SearchResultCandidate,
  refs: ReferenceItem[],
): ReferenceItem | undefined {
  if (candidate.papers_id !== null) {
    const byId = refs.find((r) => r.papers_id === candidate.papers_id);
    if (byId) return byId;
  }
  if (candidate.arxiv_id !== null) {
    const byArxiv = refs.find((r) => r.arxiv_id === candidate.arxiv_id);
    if (byArxiv) return byArxiv;
  }
  // Title fallback: covers composer-paperclip uploads where arxiv_id is null
  // on the ref and papers_id isn't on the candidate.
  const normCandidateTitle = normalizeTitle(candidate.title);
  return refs.find((r) => normalizeTitle(r.title) === normCandidateTitle);
}

interface ManualDownloadFallbackProps {
  triedUrls: string[];
  sessionId: number | null;
  candidate: SearchResultCandidate;
  /** Called after a successful PDF upload so the caller can refresh refs. */
  onAdded: () => void;
}

/** Inline card section rendered when all OA URLs failed (e.g. Cloudflare-blocked
 *  bioRxiv). Shows a friendly message, the tried links, and an "Upload PDF"
 *  button that mirrors AttachPaperMenu's PDF-upload flow. */
function ManualDownloadFallback({
  triedUrls,
  sessionId,
  candidate,
  onAdded,
}: ManualDownloadFallbackProps) {
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const appendReferenceLocal = useChatStore((s) => s.appendReferenceLocal);

  async function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file || sessionId === null) return;
    setUploading(true);
    try {
      const result = await uploadPdf(sessionId, file);
      appendReferenceLocal(sessionId, {
        papers_id: result.papers_id,
        paper_content_id: result.paper_content_id,
        enabled: true,
        added_at: new Date().toISOString(),
        arxiv_id: candidate.arxiv_id,
        title: result.title,
        year: candidate.year,
        kind: "pdf_upload",
      });
      toast.success(result.cache_hit ? "Re-attached" : "Added", {
        description: result.title,
      });
      onAdded();
    } catch (err) {
      toast.error("Upload failed", {
        description: err instanceof Error ? err.message : String(err),
      });
      if (fileInputRef.current) fileInputRef.current.value = "";
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="mt-1 space-y-1.5 rounded-md border border-amber-200 bg-amber-50 p-2.5 text-xs dark:border-amber-800 dark:bg-amber-950/30">
      <p className="text-muted-foreground">
        Couldn't auto-fetch — the source blocks automated downloads. Download
        the PDF manually and upload it:
      </p>
      <ul className="space-y-0.5">
        {triedUrls.map((url) => (
          <li key={url}>
            <a
              href={url}
              target="_blank"
              rel="noopener noreferrer"
              className="break-all text-blue-600 hover:underline dark:text-blue-400"
            >
              {url}
            </a>
          </li>
        ))}
      </ul>
      {/* Hidden file input wired to the Upload PDF button below */}
      <input
        ref={fileInputRef}
        type="file"
        accept="application/pdf"
        aria-label="Upload PDF for this paper"
        className="sr-only"
        onChange={(e) => void handleFileChange(e)}
        disabled={uploading || sessionId === null}
      />
      <Button
        size="sm"
        variant="outline"
        className="h-7 text-xs"
        disabled={uploading || sessionId === null}
        onClick={() => fileInputRef.current?.click()}
      >
        {uploading ? "Uploading…" : "Upload PDF"}
      </Button>
    </div>
  );
}

interface AddButtonProps {
  candidate: SearchResultCandidate;
  sessionId: number | null;
}

const EMPTY_REFS: ReferenceItem[] = [];

function AddButton({ candidate, sessionId }: AddButtonProps) {
  const [state, setState] = useState<"idle" | "loading" | "error">("idle");
  // Subscribe to this session's slice only — a stable EMPTY_REFS fallback
  // avoids returning a fresh array each render, which would tick the
  // Zustand "did the selector return something new?" check on every store
  // mutation and trigger an infinite re-render loop.
  const refs = useChatStore((s) =>
    sessionId !== null
      ? (s.referencesBySession[sessionId] ?? EMPTY_REFS)
      : EMPTY_REFS,
  );
  const appendReferenceLocal = useChatStore((s) => s.appendReferenceLocal);
  const setReferences = useChatStore((s) => s.setReferences);

  if (sessionId === null) {
    // Race window: backend `session` SSE event hasn't landed yet, so
    // we don't have a session id to POST against. Disable the button
    // with a tooltip so the user understands it's transient.
    return (
      <Button
        size="sm"
        variant="outline"
        disabled
        title="Establishing session — try again in a second"
      >
        Add as reference
      </Button>
    );
  }

  // Single source of truth for whether this candidate is in the session:
  // the live references slice. Frozen flags on the candidate
  // (auto_added, already_in_session) only choose which badge to render —
  // they no longer decide IF a badge is rendered.
  const matchingRef = findMatchingRef(candidate, refs);

  if (matchingRef) {
    const label =
      candidate.auto_added || candidate.finalize ? "Added by agent" : "Added";
    return (
      <Badge variant="secondary" className="whitespace-nowrap">
        {label}
      </Badge>
    );
  }

  if (candidate.error === "no_ingestible_source") {
    // When tried_urls is non-empty, the detailed fallback is rendered
    // below the title row (in SearchResultList). Show a compact badge here
    // so the action slot stays tidy.
    if ((candidate.tried_urls ?? []).length > 0) {
      return (
        <Badge variant="outline" className="whitespace-nowrap text-amber-600 border-amber-300 dark:text-amber-400 dark:border-amber-700">
          Manual download
        </Badge>
      );
    }
    return (
      <Button
        size="sm"
        variant="outline"
        disabled
        title="Neither arXiv source nor open PDF available"
      >
        Source unavailable
      </Button>
    );
  }

  if (state === "loading") {
    return (
      <Button size="sm" disabled>
        Adding…
      </Button>
    );
  }

  // SS papers without arXiv ID or open PDF can't be ingested.
  if (
    candidate.paper_id.startsWith("ss:") &&
    !candidate.arxiv_id &&
    !candidate.has_open_pdf
  ) {
    return (
      <Button
        size="sm"
        variant="outline"
        disabled
        title="No ingestible source"
      >
        Source unavailable
      </Button>
    );
  }

  async function doIngest() {
    // sessionId is narrowed non-null by the early-return guard at the
    // top of AddButton; TS can't see that across the closure boundary,
    // so we capture a local non-null alias.
    if (sessionId === null) return;
    const sid = sessionId;
    setState("loading");
    try {
      const result = await ingestPaper(sid, candidate.paper_id, {
        title: candidate.title,
        abstract: candidate.abstract,
        authors: candidate.authors,
        year: candidate.year,
      });
      // Optimistic insert so the card flips to "Added" immediately,
      // without waiting for the listSessionReferences round-trip.
      // The fetch below confirms with authoritative server state.
      appendReferenceLocal(sid, {
        papers_id: result.papers_id,
        paper_content_id: result.paper_content_id,
        enabled: true,
        added_at: new Date().toISOString(),
        arxiv_id: candidate.arxiv_id,
        title: result.title,
        year: candidate.year,
        kind: candidate.arxiv_id ? "arxiv" : "pdf_upload",
      });
      setState("idle");
      // Refresh the panel with authoritative server state.
      const refreshed = await listSessionReferences(sid);
      setReferences(sid, refreshed);
    } catch {
      setState("error");
    }
  }

  if (state === "error") {
    return (
      <Button
        size="sm"
        variant="outline"
        onClick={() => void doIngest()}
      >
        Retry
      </Button>
    );
  }

  return (
    <Button
      size="sm"
      onClick={() => void doIngest()}
    >
      Add as reference
    </Button>
  );
}

interface Props {
  candidates: SearchResultCandidate[];
  sessionId: number | null;
}

export function SearchResultList({ candidates, sessionId }: Props) {
  // Live references for this session — used to suppress the manual-download
  // fallback once the paper has been successfully attached via any route
  // (card Upload PDF button, composer paperclip, or a separate tab).
  const refs = useChatStore((s) =>
    sessionId !== null
      ? (s.referencesBySession[sessionId] ?? EMPTY_REFS)
      : EMPTY_REFS,
  );

  if (candidates.length === 0) return null;

  return (
    <section
      aria-label="Search results"
      className="mt-3 space-y-2"
    >
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        Found papers
      </p>
      {candidates.map((c, i) => {
        // Whether this candidate has a live matching reference (by id, arxiv_id,
        // or normalised title — see findMatchingRef for the match order).
        const isReferenced = findMatchingRef(c, refs) !== undefined;

        return (
          <article
            key={`${c.paper_id}-${i}`}
            className="rounded-lg border border-border bg-card p-3 space-y-1.5"
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <h4 className="text-sm font-medium leading-snug line-clamp-2">
                  {c.title}
                </h4>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {c.authors.slice(0, 3).join(", ")}
                  {c.authors.length > 3 && " et al."}
                  {c.year && (
                    <span className="ml-1.5 tabular-nums">{c.year}</span>
                  )}
                </p>
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {c.arxiv_id && (
                  <Badge variant="outline" className="text-xs">
                    arXiv
                  </Badge>
                )}
                {c.paper_id.startsWith("ss:") && !c.arxiv_id && (
                  <Badge variant="outline" className="text-xs">
                    S2
                  </Badge>
                )}
                <AddButton candidate={c} sessionId={sessionId} />
              </div>
            </div>

            {c.abstract && (
              <p className="text-xs text-muted-foreground line-clamp-3">
                {c.abstract}
              </p>
            )}

            {c.reason && (
              <p className="text-xs text-muted-foreground italic">
                <span className="not-italic font-medium">Why:</span> {c.reason}
              </p>
            )}

            {/* Manual-download fallback: rendered below the body text when all
                OA URLs failed. Hidden once the paper appears in live session
                references (matched by id, arxiv_id, or normalised title) so
                that an upload via ANY route (card button, composer paperclip,
                another tab) clears the amber warning automatically. */}
            {!isReferenced &&
              c.error === "no_ingestible_source" &&
              (c.tried_urls ?? []).length > 0 && (
                <ManualDownloadFallback
                  triedUrls={c.tried_urls!}
                  sessionId={sessionId}
                  candidate={c}
                  onAdded={() => {
                    if (sessionId === null) return;
                    listSessionReferences(sessionId)
                      .then((refs) =>
                        useChatStore.getState().setReferences(sessionId, refs),
                      )
                      .catch(() => {
                        /* best-effort refresh */
                      });
                  }}
                />
              )}
          </article>
        );
      })}
    </section>
  );
}
