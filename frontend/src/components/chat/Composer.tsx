import { KeyboardEvent, useRef } from "react";
import {
  BookOpen,
  Presentation,
  Columns2,
  Send,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";

interface Props {
  onSubmit: (text: string) => void;
  disabled: boolean;
}

interface Capability {
  icon: typeof BookOpen;
  label: string;
  tooltip: string;
}

const CAPABILITIES: Capability[] = [
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
  const draft = useChatStore((s) => s.composerDraft);
  const setDraft = useChatStore((s) => s.setComposerDraft);
  const toggleCanvas = useCanvasStore((s) => s.toggleCanvas);
  const canvasOpen = useCanvasStore((s) => s.open);
  const ref = useRef<HTMLTextAreaElement>(null);

  const value = draft;
  const setValue = setDraft;

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setDraft("");
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
      className="shrink-0 border-t border-border bg-card p-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="max-w-3xl mx-auto">
        {/* Single rounded container — textarea on top, tool row + send on bottom.
            focus-within ring unifies the visual treatment across child focus. */}
        <div className="rounded-2xl border border-input bg-background shadow-sm transition-shadow focus-within:ring-2 focus-within:ring-ring">
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask about a paper, search, or just chat… (Enter to send, Shift+Enter for new line)"
            rows={2}
            className="block w-full resize-none bg-transparent px-4 pt-3 pb-1 text-sm placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            disabled={disabled}
            aria-label="Message"
          />
          <div className="flex items-center justify-between gap-1 px-2 pb-2">
            <TooltipProvider>
              <div className="flex items-center gap-0.5">
                <AttachPaperMenu />
                <Tooltip>
                  <TooltipTrigger
                    render={<span tabIndex={0} className="inline-flex" />}
                  >
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => toggleCanvas()}
                      aria-pressed={canvasOpen}
                      className={
                        canvasOpen
                          ? "h-8 w-8 bg-accent text-foreground"
                          : "h-8 w-8 text-muted-foreground hover:text-foreground"
                      }
                      aria-label="References"
                    >
                      <BookOpen className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>Toggle the reference reading panel</p>
                  </TooltipContent>
                </Tooltip>
                {CAPABILITIES.map(({ icon: Icon, label, tooltip }) => (
                  <Tooltip key={label}>
                    <TooltipTrigger
                      render={<span tabIndex={0} className="inline-flex" />}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        disabled
                        className="h-8 w-8 pointer-events-none text-muted-foreground"
                        aria-label={label}
                      >
                        <Icon className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      <p>{tooltip}</p>
                    </TooltipContent>
                  </Tooltip>
                ))}
              </div>
            </TooltipProvider>
            <Button
              type="submit"
              size="icon"
              disabled={disabled || value.trim().length === 0}
              aria-label="Send"
              className="h-8 w-8 rounded-full"
            >
              <Send className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </div>
    </form>
  );
}
