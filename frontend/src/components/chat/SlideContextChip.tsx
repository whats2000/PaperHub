import { Eye, EyeOff } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
  const { t } = useTranslation("chat");
  const hint = attached
    ? t("slideChip.attachedHint", { page })
    : t("slideChip.detachedHint", { page });
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger render={<span tabIndex={0} className="inline-flex" />}>
          <button
            type="button"
            onClick={onToggle}
            aria-pressed={attached}
            aria-label={hint}
            className={
              "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs " +
              "transition-colors " +
              (attached
                ? "border-ring bg-accent text-foreground"
                : "border-input bg-muted/40 text-muted-foreground hover:text-foreground")
            }
          >
            {attached ? <Eye className="h-3.5 w-3.5" /> : <EyeOff className="h-3.5 w-3.5" />}
            <span>{t("slideChip.label", { page })}</span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="top">
          <p>{hint}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
