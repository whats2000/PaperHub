import { useEffect, useRef, useState } from "react";
import { Pencil, Trash2, Check, X, Plus } from "lucide-react";

import type { MemoryItem, MemoryScope } from "@/types/domain";
import { MemoryGateRefused } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useMemoriesStore } from "@/store/memories";

interface Props {
  sessionId: number;
}

/** Stable empty array — returned by the per-session selector when no memories
 *  have been fetched yet.  Must not be an inline literal (`?? []`) because
 *  Zustand's useSyncExternalStore calls getSnapshot on every render and a new
 *  `[]` reference would trigger an infinite re-render loop. */
const EMPTY_MEMORIES: MemoryItem[] = [];

/** One row in the memory list — handles its own edit mode. */
function MemoryRow({
  memory,
  sessionId,
}: {
  memory: MemoryItem;
  sessionId: number;
}) {
  const patchMemoryLocal = useMemoriesStore((s) => s.patchMemoryLocal);
  const deleteMemoryLocal = useMemoriesStore((s) => s.deleteMemoryLocal);

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(memory.content);
  const [busy, setBusy] = useState(false);

  const isSuperseded = memory.status === "superseded";

  async function handleSave() {
    setBusy(true);
    try {
      await patchMemoryLocal(sessionId, memory.id, { content: draft });
      setEditing(false);
    } finally {
      setBusy(false);
    }
  }

  function handleCancelEdit() {
    setDraft(memory.content);
    setEditing(false);
  }

  async function handleToggleStatus() {
    setBusy(true);
    try {
      await patchMemoryLocal(sessionId, memory.id, {
        status: isSuperseded ? "active" : "superseded",
      });
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    setBusy(true);
    try {
      await deleteMemoryLocal(sessionId, memory.id);
    } finally {
      setBusy(false);
    }
  }

  return (
    <li
      className={`flex flex-col gap-1 px-3 py-2.5 border-b border-border last:border-0 ${
        isSuperseded ? "opacity-50" : ""
      }`}
    >
      {/* Content area */}
      {editing ? (
        <div className="flex flex-col gap-1.5">
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={busy}
            aria-label="Edit memory content"
            className="text-xs min-h-0"
          />
          <div className="flex gap-1 justify-end">
            <Button
              type="button"
              size="icon-xs"
              variant="default"
              disabled={busy || draft.trim() === ""}
              onClick={() => void handleSave()}
              aria-label="save"
            >
              <Check className="h-3 w-3" />
            </Button>
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              disabled={busy}
              onClick={handleCancelEdit}
              aria-label="cancel"
            >
              <X className="h-3 w-3" />
            </Button>
          </div>
        </div>
      ) : (
        <p className="text-xs leading-snug">{memory.content}</p>
      )}

      {/* Meta row: badge + supersede links + action buttons */}
      <div className="flex items-center gap-1.5 flex-wrap">
        {/* Status badge */}
        <Badge
          variant={isSuperseded ? "outline" : "secondary"}
          className="text-xs px-1 h-4"
        >
          {memory.status}
        </Badge>

        {/* Supersede chain links */}
        {memory.supersedes !== null && (
          <span className="text-xs text-muted-foreground">
            supersedes #{memory.supersedes}
          </span>
        )}
        {memory.superseded_by !== null && (
          <span className="text-xs text-muted-foreground">
            superseded by #{memory.superseded_by}
          </span>
        )}

        {/* Spacer */}
        <span className="flex-1" />

        {/* Action buttons (hidden while editing) */}
        {!editing && (
          <>
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              disabled={busy}
              onClick={() => { setDraft(memory.content); setEditing(true); }}
              aria-label="edit"
              className="text-muted-foreground hover:text-foreground"
            >
              <Pencil className="h-3 w-3" />
            </Button>
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              disabled={busy}
              onClick={() => void handleToggleStatus()}
              aria-label={isSuperseded ? "reactivate" : "deactivate"}
              className="text-muted-foreground hover:text-foreground"
            >
              {isSuperseded ? (
                <Check className="h-3 w-3" />
              ) : (
                <X className="h-3 w-3" />
              )}
            </Button>
            <Button
              type="button"
              size="icon-xs"
              variant="ghost"
              disabled={busy}
              onClick={() => void handleDelete()}
              aria-label="delete"
              className="text-muted-foreground hover:text-destructive"
            >
              <Trash2 className="h-3 w-3" />
            </Button>
          </>
        )}
      </div>
    </li>
  );
}

/** Inline composer for adding a new memory entry. Shows a textarea, a scope
 *  toggle (Project/User), an Add button, and an inline error when the safety
 *  gate refuses the content. */
function AddMemoryComposer({ sessionId }: { sessionId: number }) {
  const addMemoryLocal = useMemoriesStore((s) => s.addMemoryLocal);
  const [content, setContent] = useState("");
  const [scope, setScope] = useState<MemoryScope>("session");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  async function handleAdd() {
    const trimmed = content.trim();
    if (!trimmed) return;
    setBusy(true);
    setError(null);
    try {
      await addMemoryLocal(sessionId, trimmed, scope);
      setContent("");
      textareaRef.current?.focus();
    } catch (err) {
      if (err instanceof MemoryGateRefused) {
        setError(`Couldn't save: ${err.reason}`);
      } else {
        setError("Couldn't save memory — please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="px-3 py-2 border-b border-border">
      <Textarea
        ref={textareaRef}
        value={content}
        onChange={(e) => setContent(e.target.value)}
        disabled={busy}
        placeholder="Add a memory…"
        aria-label="New memory content"
        className="text-xs min-h-0 mb-1.5 resize-none"
        rows={2}
      />
      <div className="flex items-center gap-1.5 flex-wrap">
        {/* Scope toggle */}
        <div
          role="group"
          aria-label="Memory scope"
          className="flex rounded border border-border overflow-hidden text-xs"
        >
          <button
            type="button"
            disabled={busy}
            onClick={() => setScope("session")}
            aria-pressed={scope === "session"}
            className={`px-2 py-0.5 transition-colors ${
              scope === "session"
                ? "bg-accent text-foreground font-medium"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            Project (session)
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => setScope("global")}
            aria-pressed={scope === "global"}
            className={`px-2 py-0.5 transition-colors border-l border-border ${
              scope === "global"
                ? "bg-accent text-foreground font-medium"
                : "text-muted-foreground hover:bg-muted"
            }`}
          >
            User (global)
          </button>
        </div>
        <span className="flex-1" />
        <Button
          type="button"
          size="icon-xs"
          variant="default"
          disabled={busy || content.trim() === ""}
          onClick={() => void handleAdd()}
          aria-label="Add memory"
        >
          <Plus className="h-3 w-3" />
        </Button>
      </div>
      {error !== null && (
        <p role="alert" className="mt-1 text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
}

/** Section with a heading label and a list of memory rows. Renders nothing if
 *  the items array is empty. */
function MemorySection({
  label,
  items,
  sessionId,
}: {
  label: string;
  items: MemoryItem[];
  sessionId: number;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <h3 className="px-3 py-1.5 text-xs font-semibold text-muted-foreground uppercase tracking-wide border-b border-border">
        {label}
      </h3>
      <ul>
        {items.map((m) => (
          <MemoryRow key={m.id} memory={m} sessionId={sessionId} />
        ))}
      </ul>
    </div>
  );
}

/**
 * MemoryManager panel — lists all memories for a session, grouped by scope.
 *
 * - "Project (session)" section: memories with scope="session"
 * - "User (global)" section: memories with scope="global"
 *
 * Each row shows the content, status badge, supersede-chain links, and per-row
 * controls: edit content, toggle active↔superseded, delete.
 */
export function MemoryManager({ sessionId }: Props) {
  const fetchMemories = useMemoriesStore((s) => s.fetchMemories);
  const memories = useMemoriesStore(
    (s) => s.memoriesBySession[sessionId] ?? EMPTY_MEMORIES,
  );

  useEffect(() => {
    void fetchMemories(sessionId);
  }, [sessionId, fetchMemories]);

  const sessionScoped = memories.filter((m) => m.scope === "session");
  const globalScoped = memories.filter((m) => m.scope === "global");
  const hasAny = memories.length > 0;

  return (
    <div className="flex flex-col h-full overflow-y-auto">
      <AddMemoryComposer sessionId={sessionId} />
      {!hasAny ? (
        <p className="text-xs text-muted-foreground text-center px-4 py-8">
          No memories yet — facts the assistant learns during your sessions will
          appear here.
        </p>
      ) : (
        <>
          <MemorySection
            label="Project (session)"
            items={sessionScoped}
            sessionId={sessionId}
          />
          <MemorySection
            label="User (global)"
            items={globalScoped}
            sessionId={sessionId}
          />
        </>
      )}
    </div>
  );
}
