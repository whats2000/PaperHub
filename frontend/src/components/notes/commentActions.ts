/** Resolve and Delete on a comment thread, each with an Undo.
 *
 * Both go to the server at once. Undo reopens the thread there and puts the
 * underline back over the thread's quote in the note as it is now.
 */
import { toast } from "sonner";

type Translate = (key: string, fallback: string) => string;

const UNDO_MS = 5000;

export interface ThreadActionDeps {
  kind: "resolve" | "delete";
  /** Takes the comment off the words. */
  strip: () => void;
  /** Puts it back (over the quote in the current text). */
  restore: () => void;
  setStatus: (status: "open" | "resolved" | "deleted") => Promise<unknown>;
  t: Translate;
}

/** Resolve or delete, with a toast whose Undo reopens the thread and whose
 * next button reads "Resolve again" / "Delete again". */
export function runThreadAction(deps: ThreadActionDeps): void {
  const { kind, strip, restore, setStatus, t } = deps;
  const resolve = kind === "resolve";
  const done = resolve
    ? t("notes:comment.resolved", "Comment resolved")
    : t("notes:comment.deleted", "Comment deleted");
  const again = resolve
    ? t("notes:comment.resolveAgain", "Resolve again")
    : t("notes:comment.deleteAgain", "Delete again");
  const fail = () => void toast.error(t("notes:comment.failed", "Couldn't save the comment."));

  const apply = () => {
    strip();
    void setStatus(resolve ? "resolved" : "deleted").catch(fail);
    let undone = false;
    toast(done, {
      duration: UNDO_MS,
      action: {
        label: t("notes:comment.undo", "Undo"),
        onClick: () => {
          if (undone) return;
          undone = true;
          void setStatus("open").catch(fail);
          restore();
          toast(t("notes:comment.restored", "Comment restored"), {
            duration: UNDO_MS,
            action: { label: again, onClick: apply },
          });
        },
      },
    });
  };
  apply();
}
