import { useState } from "react";
import { BarChart3, Download } from "lucide-react";
import { useTranslation } from "react-i18next";

import { ConfirmQueue } from "@/components/feedback/ConfirmQueue";
import { ExportDialog } from "@/components/feedback/ExportDialog";
import { StageAnalysis } from "@/components/feedback/StageAnalysis";
import { PageHeader } from "@/components/layout/PageHeader";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function FeedbackPage() {
  const { t } = useTranslation(["states", "feedback"]);
  const [expanded, setExpanded] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  // Bumped after the queue submits, so the analysis counts the new answers.
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="flex flex-1 min-h-0 flex-col animate-in fade-in-0 duration-150 motion-reduce:animate-none">
      <PageHeader
        icon={BarChart3}
        title={t("states:nav.feedback", "Feedback")}
        actions={
          <Button variant="outline" size="sm" onClick={() => setExportOpen(true)}>
            <Download />
            {t("feedback:export.open", "Export training data")}
          </Button>
        }
      />
      <ExportDialog open={exportOpen} onOpenChange={setExportOpen} />
      <ConfirmQueue
        expanded={expanded}
        onExpandedChange={setExpanded}
        onSubmitted={() => setRefreshKey((n) => n + 1)}
      />
      {/* The stage analysis. The expanded queue card pushes it out:
          its flex-grow eases 1 -> 0 while the card's eases 0 -> 1. */}
      <section
        data-testid="feedback-analysis"
        aria-hidden={expanded || undefined}
        inert={expanded}
        className={cn(
          "flex min-h-0 basis-0 flex-col overflow-hidden transition-[flex-grow] duration-[280ms] ease-out motion-reduce:transition-none",
          expanded ? "grow-0" : "grow",
        )}
      >
        <StageAnalysis refreshKey={refreshKey} />
      </section>
    </div>
  );
}
