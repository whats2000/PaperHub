import { ShieldAlert } from "lucide-react";

export function RejectionPill({ reason }: { reason: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full bg-yellow-100 dark:bg-yellow-900/30 text-yellow-900 dark:text-yellow-200 px-2 py-0.5 text-xs">
      <ShieldAlert className="h-3 w-3" /> Rejected: {reason}
    </span>
  );
}
