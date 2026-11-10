import { CornerDownRight } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

/**
 * Follow-ups the agent proposed (its `<proposal>` lines, ruling R12). Each
 * button sends its text as a new message, which starts a normal turn.
 */
export function ProposalButtons({
  proposals,
  onSend,
  disabled = false,
}: {
  proposals: string[];
  onSend: (text: string) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("chat");
  if (proposals.length === 0) return null;
  return (
    <div
      role="group"
      aria-label={t("proposals.label")}
      className="not-prose mt-3 flex flex-wrap gap-2 animate-in fade-in-0 slide-in-from-bottom-1 duration-200 motion-reduce:animate-none"
    >
      {proposals.map((text, i) => (
        <Button
          key={`${i}-${text}`}
          type="button"
          size="sm"
          variant="outline"
          disabled={disabled}
          onClick={() => onSend(text)}
          className="h-auto max-w-full gap-1.5 whitespace-normal py-1 text-left text-xs"
        >
          <CornerDownRight className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{text}</span>
        </Button>
      ))}
    </div>
  );
}
