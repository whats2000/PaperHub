import { KeyboardEvent, useRef, useState } from "react";
import {
  Paperclip,
  BookOpen,
  Presentation,
  Columns2,
  Send,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

interface Props {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

interface Capability {
  icon: typeof Paperclip;
  label: string;
  tooltip: string;
}

const CAPABILITIES: Capability[] = [
  {
    icon: Paperclip,
    label: "Attach paper",
    tooltip: "Coming in Plan C — upload PDF or paste arXiv ID",
  },
  {
    icon: BookOpen,
    label: "References",
    tooltip:
      "Coming in Plan D — toggle which papers are in scope for this turn",
  },
  {
    icon: Presentation,
    label: "Slides",
    tooltip: "Coming in Plan F — generate slides from the cited papers",
  },
  {
    icon: Columns2,
    label: "Compare",
    tooltip: "Coming in Plan G — fan out the same prompt to two models",
  },
];

export function Composer({ onSubmit, disabled }: Props) {
  const [value, setValue] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue("");
    ref.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Plain Enter submits; Shift+Enter allows default (newline).
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      className="border-t border-border bg-card p-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="relative max-w-3xl mx-auto">
        <Textarea
          ref={ref}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Ask about a paper, search, or just chat… (Enter to send, Shift+Enter for new line)"
          rows={2}
          className="resize-none pr-12"
          disabled={disabled}
          aria-label="Message"
        />
        <Button
          type="submit"
          size="icon"
          variant="ghost"
          disabled={disabled || value.trim().length === 0}
          aria-label="Send"
          className="absolute right-2 bottom-2 h-8 w-8"
        >
          <Send className="h-4 w-4" />
        </Button>

        {/* Capability action bar */}
        <div className="mt-2 flex items-center gap-1 px-1">
          <TooltipProvider>
            {CAPABILITIES.map(({ icon: Icon, label, tooltip }) => (
              <Tooltip key={label}>
                <TooltipTrigger
                  render={<span tabIndex={0} className="inline-flex" />}
                >
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled
                    className="gap-1.5 pointer-events-none"
                    aria-label={label}
                  >
                    <Icon className="h-3.5 w-3.5" />
                    {label}
                  </Button>
                </TooltipTrigger>
                <TooltipContent side="top">
                  <p>{tooltip}</p>
                </TooltipContent>
              </Tooltip>
            ))}
          </TooltipProvider>
        </div>
      </div>
    </form>
  );
}
