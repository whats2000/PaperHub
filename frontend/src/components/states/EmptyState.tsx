import { MessageSquare, Search, BookOpen, Presentation, BarChart3 } from "lucide-react";

import { useChatStore } from "@/store/chat";

interface Prompt {
  icon: typeof MessageSquare;
  label: string;
  prompt: string;
}

const PROMPTS: Prompt[] = [
  {
    icon: Search,
    label: "Find papers",
    prompt: "Find recent papers on mixture-of-experts routing",
  },
  {
    icon: BookOpen,
    label: "Compare papers",
    prompt: "How do these two papers differ on expert collapse?",
  },
  {
    icon: Presentation,
    label: "Generate slides",
    prompt: "Make slides comparing the methodology of the enabled papers",
  },
  {
    icon: BarChart3,
    label: "Library stats",
    prompt: "How many papers did I add this week?",
  },
];

export function EmptyState() {
  const setDraft = useChatStore((s) => s.setComposerDraft);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4">
      <div className="flex flex-col items-center gap-2 text-muted-foreground">
        <MessageSquare className="h-12 w-12" />
        <p className="text-sm">What can I help you with?</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-xl w-full">
        {PROMPTS.map(({ icon: Icon, label, prompt }) => (
          <button
            key={label}
            type="button"
            onClick={() => setDraft(prompt)}
            className="group text-left rounded-lg border border-border bg-card hover:bg-accent transition-colors p-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <div className="flex items-center gap-2 text-xs text-muted-foreground mb-1">
              <Icon className="h-3.5 w-3.5" />
              {label}
            </div>
            <div className="text-sm text-foreground">{prompt}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
