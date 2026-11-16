/** Resolve and Delete on a comment thread, each with an Undo.
 *
 * A deleted thread cannot come back on the server, so Delete waits for its
 * toast to run out (or the editor to close) before telling the server; Undo
 * inside that window just restores the words.
 */
import { toast } from "sonner";

type Translate = (key: string, fallback: string) => string;

const UNDO_MS = 5000;

/** Deletes whose toast is still open: the server is told when it closes. */
const pendingDeletes = new Set<() => void>();

/** Tells the server about deletes still waiting on their toast (the editor
 * closing must not lose them). */
export function flushPendingThreadActions(): void {
  for (const finish of [...pendingDeletes]) finish();
}

export interface ThreadActionDeps {
  kind: "resolve" | "delete";
  /** Takes the comment off the words; returns what puts it back. */
  strip: () => () => void;
  setStatus: (status: "open" | "resolved" | "deleted") => Promise<unknown>;
  t: Translate;
}

/** Resolve or delete, with a toast whose Undo restores the words and whose
 * next button reads "Resolve again" / "Delete again". */
export function runThreadAction(deps: ThreadActionDeps): void {
  const { kind, strip, setStatus, t } = deps;
  const resolve = kind === "resolve";
  const done = resolve
    ? t("notes:comment.resolved", "Comment resolved")
    : t("notes:comment.deleted", "Comment deleted");
  const again = resolve
    ? t("notes:comment.resolveAgain", "Resolve again")
    : t("notes:comment.deleteAgain", "Delete again");
  const fail = () => void toast.error(t("notes:comment.failed", "Couldn't save the comment."));

  const apply = () => {
    const restore = strip();
    let applied = true;
    let sent = false;
    const finish = () => {
      pendingDeletes.delete(finish);
      if (!applied || sent || resolve) return;
      sent = true;
      void setStatus("deleted").catch(fail);
    };
    if (resolve) void setStatus("resolved").catch(fail);
    else pendingDeletes.add(finish);
    const undo = () => {
      if (!applied || sent) return;
      applied = false;
      pendingDeletes.delete(finish);
      restore();
      if (resolve) void setStatus("open").catch(fail);
      toast(t("notes:comment.restored", "Comment restored"), {
        duration: UNDO_MS,
        action: { label: again, onClick: apply },
      });
    };
    toast(done, {
      duration: UNDO_MS,
      action: { label: t("notes:comment.undo", "Undo"), onClick: undo },
      onAutoClose: finish,
      onDismiss: finish,
    });
  };
  apply();
}
