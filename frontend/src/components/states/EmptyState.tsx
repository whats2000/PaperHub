import { MessageSquare, Search, BookOpen, Presentation, BarChart3 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useChatStore } from "@/store/chat";

interface Prompt {
  icon: typeof MessageSquare;
  labelKey: string;
  promptKey: string;
}

const PROMPTS: Prompt[] = [
  {
    icon: Search,
    labelKey: "empty.findPapers",
    promptKey: "empty.findPapersPrompt",
  },
  {
    icon: BookOpen,
    labelKey: "empty.comparePapers",
    promptKey: "empty.comparePapersPrompt",
  },
  {
    icon: Presentation,
    labelKey: "empty.generateSlides",
    promptKey: "empty.generateSlidesPrompt",
  },
  {
    icon: BarChart3,
    labelKey: "empty.libraryStats",
    promptKey: "empty.libraryStatsPrompt",
  },
];

export function EmptyState() {
  const { t } = useTranslation("states");
  const setDraft = useChatStore((s) => s.setComposerDraft);
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-4">
      <div className="flex flex-col items-center gap-2 text-muted-foreground">
        <MessageSquare className="h-12 w-12" />
        <p className="text-sm">{t("empty.heading")}</p>
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 max-w-xl w-full">
        {PROMPTS.map(({ icon: Icon, labelKey, promptKey }) => {
          const label = t(labelKey);
          const prompt = t(promptKey);
          return (
          <button
            key={labelKey}
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
          );
        })}
      </div>
    </div>
  );
}
