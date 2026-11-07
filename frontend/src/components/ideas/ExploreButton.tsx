import { Loader2, Shuffle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { usePresence } from "@/hooks/usePresence";
import { useIdeationStore } from "@/store/ideation";

/**
 * Free exploration's "find new pairs" action: starts an explore run, waits for
 * it, and says so when the re-run kept nothing new (Ruling U3). Shown only in
 * the free-exploration scope; it fades in and out with the scope switch.
 */
export function ExploreButton() {
  const { t } = useTranslation("ideas");
  const show = useIdeationStore((s) => s.scope === "explore");
  const exploring = useIdeationStore((s) => s.exploring);
  const explore = useIdeationStore((s) => s.explore);
  const { mounted, state } = usePresence(show);

  const run = async () => {
    let status;
    try {
      status = await explore();
    } catch {
      toast.error(t("explore.failed", "Free exploration failed"));
      return;
    }
    if (!status) return; // left the page
    if (status.last_error) toast.error(t("explore.failed", "Free exploration failed"));
    else if (status.last_new_count === 0) toast(t("explore.noNew", "No new pairs this time"));
    else if (status.last_new_count)
      toast.success(t("explore.found", "{{count}} new pairs", { count: status.last_new_count }));
  };

  if (!mounted) return null;
  return (
    <div
      data-state={state}
      inert={!show}
      className="ml-auto shrink-0 pt-2 pr-5 duration-150 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 motion-reduce:animate-none"
    >
      <Button variant="outline" size="sm" disabled={exploring} onClick={() => void run()}>
        {exploring ? (
          <Loader2 className="animate-spin motion-reduce:animate-none" aria-hidden="true" />
        ) : (
          <Shuffle aria-hidden="true" />
        )}
        {exploring ? t("explore.running", "Finding pairs…") : t("explore.run", "Find new pairs")}
      </Button>
    </div>
  );
}
