import { Lightbulb } from "lucide-react";
import { useTranslation } from "react-i18next";

import { PageHeader } from "@/components/layout/PageHeader";

export function IdeasPage() {
  const { t } = useTranslation("states");
  return (
    <div className="flex flex-1 min-h-0 flex-col animate-in fade-in-0 duration-150 motion-reduce:animate-none">
      <PageHeader icon={Lightbulb} title={t("nav.ideas", "Ideas")} />
      <p className="p-5 text-sm text-muted-foreground">
        {t("ideasPage.empty", "Ideas will appear here.")}
      </p>
    </div>
  );
}
