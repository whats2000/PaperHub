import { useState } from "react";

import type { SearchResultCandidate } from "@/types/domain";
import { ingestPaper } from "@/lib/api";
import { useChatStore } from "@/store/chat";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

interface AddButtonProps {
  candidate: SearchResultCandidate;
  sessionId: number;
}

function AddButton({ candidate, sessionId }: AddButtonProps) {
  const [state, setState] = useState<"idle" | "loading" | "added" | "error">(
    "idle",
  );
  const addedPaperIds = useChatStore((s) => s.addedPaperIds);
  const markPaperAdded = useChatStore((s) => s.markPaperAdded);

  if (candidate.auto_added) {
    return (
      <Badge variant="secondary" className="whitespace-nowrap">
        Added by agent
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

  if (candidate.already_in_session || addedPaperIds.has(candidate.paper_id) || state === "added") {
    return (
      <Badge variant="secondary" className="whitespace-nowrap">
        Added
      </Badge>
    );
  }

  if (state === "loading") {
    return (
      <Button size="sm" disabled>
        Adding…
      </Button>
    );
  }

  // SS papers without arXiv ID or open PDF can't be ingested yet (v2.4-5 handles it)
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
    setState("loading");
    try {
      await ingestPaper(sessionId, candidate.paper_id);
      markPaperAdded(candidate.paper_id);
      setState("added");
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
  sessionId: number;
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
