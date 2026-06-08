import { ShieldAlert } from "lucide-react";
import { useTranslation } from "react-i18next";

export function RejectionPill({ reason }: { reason: string }) {
  const { t } = useTranslation("states");
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-yellow-100 dark:bg-yellow-900/30 text-yellow-900 dark:text-yellow-200 px-2 py-0.5 text-xs">
      <ShieldAlert className="h-3 w-3" /> {t("rejection.label", { reason })}
    </span>
  );
}
