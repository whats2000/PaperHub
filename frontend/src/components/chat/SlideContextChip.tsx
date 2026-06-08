import { Eye, EyeOff } from "lucide-react";

interface Props {
  /** 1-based page of the on-screen slide (content tracks the active slide). */
  page: number;
  /** Whether the slide is attached as chat context (eye open). */
  attached: boolean;
  /** Toggle attachment. Does NOT change which slide is shown. */
  onToggle: () => void;
}

/** Composer context chip for the on-screen slide. Always rendered while a deck
 *  is in view (content tracks the active slide even when detached); the eye
 *  toggles whether the slide is attached as chat context. */
export function SlideContextChip({ page, attached, onToggle }: Props) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={attached}
      aria-label={attached ? "Slide attached as context — click to detach"
                           : "Slide detached — click to attach as context"}
      className={
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs " +
        "transition-colors " +
        (attached
          ? "border-ring bg-accent text-foreground"
          : "border-input bg-muted/40 text-muted-foreground hover:text-foreground")
      }
    >
      {attached ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
      <span>Slide {page}</span>
    </button>
  );
}
