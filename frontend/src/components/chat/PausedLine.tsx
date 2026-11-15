import { Clock } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { usePresence } from "@/hooks/usePresence";

/**
 * H5a: "Paused · N items wait for your OK…" where the answer will stream,
 * while the run waits on its cards. It grows open when the run pauses and
 * collapses (height, 200 ms) when it resumes, so the answer glides in; it
 * keeps its last count while leaving. The clock pulses (static under
 * reduced motion).
 */
export function PausedLine({ count }: { count: number }) {
  const { t } = useTranslation("chat");
  const presence = usePresence(count > 0, 200);
  const [shown, setShown] = useState(count);
  if (count > 0 && count !== shown) setShown(count);
  if (!presence.mounted) return null;
  return (
    <div
      data-testid="paused-line"
      data-state={presence.state}
      className="grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none data-[state=open]:grid-rows-[1fr] data-[state=open]:opacity-100 starting:data-[state=open]:grid-rows-[0fr] starting:data-[state=open]:opacity-0 data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0"
    >
      <div className="min-h-0 overflow-hidden">
        <p
          role="status"
          className="not-prose mt-2 flex items-center gap-1.5 text-xs text-amber-700 dark:text-amber-300"
        >
          <Clock className="h-3.5 w-3.5 shrink-0 motion-safe:animate-pulse" aria-hidden />
          <span>
            {t(
              "paused.line",
              "Paused · {{count}} items wait for your OK. I'll continue as soon as you decide.",
              { count: shown },
            )}
          </span>
        </p>
      </div>
    </div>
  );
}
