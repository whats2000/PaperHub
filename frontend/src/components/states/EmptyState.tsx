import { MessageSquare } from "lucide-react";

export function EmptyState() {
  return (
    <div className="flex flex-1 flex-col items-center justify-center text-muted-foreground gap-3">
      <MessageSquare className="h-12 w-12" />
      <p className="text-sm">
        Start a conversation. Try: &quot;What can you help me with?&quot;
      </p>
    </div>
  );
}
