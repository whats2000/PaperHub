import { useState } from "react";

import type {
  ReferenceItem,
  SearchResultCandidate,
} from "@/types/domain";
import { ingestPaper, listSessionReferences } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

/**
 * Find the reference row (if any) that corresponds to a search candidate.
 *
 * Match order: `papers_id` first (carried for agent-finalized + post-ingest
 * candidates), then `arxiv_id` as the bridging identity when papers_id
 * isn't known to the candidate (e.g. before the candidate has been patched
 * with the ingest response).
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
    return refs.find((r) => r.arxiv_id === candidate.arxiv_id);
  }
  return undefined;
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
  if (candidates.length === 0) return null;

  return (
    <section
      aria-label="Search results"
      className="mt-3 space-y-2"
    >
      <p className="text-xs font-medium text-muted-foreground uppercase tracking-wide">
        Found papers
      </p>
      {candidates.map((c) => (
        <article
          key={c.paper_id}
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
        </article>
      ))}
    </section>
  );
}
