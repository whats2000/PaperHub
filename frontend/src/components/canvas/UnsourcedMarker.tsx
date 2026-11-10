import { CircleHelp } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { UNSOURCED_REASONS } from "@/lib/unsourcedMarks";
import { cn } from "@/lib/utils";

interface Props {
  reason: string;
}

/** Superscript mark where a citation failed validation: the claim beside it
 * has no source the reader can check. Clicking it explains why, so the user
 * always knows which statements are unverified. */
export function UnsourcedMarker({ reason }: Props) {
  const { t } = useTranslation("canvas");
  const [open, setOpen] = useState(false);
  const known = (UNSOURCED_REASONS as readonly string[]).includes(reason);
  const why = known
    ? t(`unsourced.reason.${reason}`)
    : t("unsourced.reason.unknown", "the source could not be checked");
  return (
    <sup>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger
          render={
            <button
              type="button"
              aria-label={t("unsourced.aria", { reason: why })}
              className={cn(
                "mx-0.5 inline-flex cursor-pointer items-center rounded border border-dashed border-muted-foreground/60 px-0.5 text-[0.7em] text-muted-foreground",
                "transition-colors duration-150 motion-reduce:transition-none",
                "hover:border-foreground hover:text-foreground",
                open && "border-foreground text-foreground",
              )}
            />
          }
        >
          <CircleHelp className="h-[1em] w-[1em]" aria-hidden />
        </PopoverTrigger>
        <PopoverContent
          side="top"
          className="w-72 max-w-[calc(100vw-2rem)] origin-(--transform-origin) p-3 text-xs leading-relaxed duration-150 motion-reduce:animate-none"
        >
          {t("unsourced.explain", { reason: why })}
        </PopoverContent>
      </Popover>
    </sup>
  );
}
