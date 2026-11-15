import { useState } from "react";
import { Check, Hand, Zap, type LucideIcon } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { setSessionAllowSearches, setSessionAutoApprove } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useChatStore } from "@/store/chat";

type Mode = "ask" | "auto";
const MODES: { mode: Mode; icon: LucideIcon }[] = [
  { mode: "ask", icon: Hand },
  { mode: "auto", icon: Zap },
];

/**
 * Per-chat approval mode (H4) in the composer's tool row: a chip that opens
 * a menu. "Ask first" (default): confirm-tier actions (remove a paper,
 * forget a memory) ask the user. "Auto": a quick check decides whether they
 * can run without asking.
 */
export function AutoApproveToggle() {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const auto = useChatStore(
    (s) =>
      s.sessions.find((x) => x.id === s.activeSessionId)?.auto_approve ?? false,
  );
  const allowSearches = useChatStore(
    (s) =>
      s.sessions.find((x) => x.id === s.activeSessionId)?.allow_searches ?? false,
  );
  const current: Mode = auto ? "auto" : "ask";

  // H5a: "Allow searches in this chat" (from a search card) turned off.
  const askSearchesAgain = async () => {
    const store = useChatStore.getState();
    const localId = activeSessionId;
    if (localId === null) return;
    store.setAllowSearches(localId, false);
    try {
      const backendId = await store.ensureBackendSession(localId);
      const res = await setSessionAllowSearches(backendId, false);
      useChatStore.getState().setAllowSearches(localId, res.allow_searches);
    } catch {
      useChatStore.getState().setAllowSearches(localId, true);
      toast.error(t("autoApprove.failed"));
    }
  };

  const change = async (next: boolean) => {
    const store = useChatStore.getState();
    const localId = activeSessionId ?? store.newSession();
    store.setAutoApprove(localId, next);
    setSaving(true);
    try {
      const backendId = await useChatStore.getState().ensureBackendSession(localId);
      const res = await setSessionAutoApprove(backendId, next);
      useChatStore.getState().setAutoApprove(localId, res.auto_approve);
    } catch {
      useChatStore.getState().setAutoApprove(localId, !next);
      toast.error(t("autoApprove.failed"));
    } finally {
      setSaving(false);
    }
  };

  const select = (mode: Mode) => {
    setOpen(false);
    if (mode !== current) void change(mode === "auto");
  };

  const CurrentIcon = auto ? Zap : Hand;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <button
            type="button"
            disabled={saving}
            aria-label={`${t("autoApprove.menuTitle")}: ${t(`autoApprove.${current}.title`)}`}
            className={cn(
              "inline-flex h-8 items-center gap-1 rounded-full px-2.5 text-xs font-medium",
              "transition-colors duration-200 motion-reduce:transition-none",
              "focus:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50",
              auto || open
                ? "bg-accent text-foreground"
                : "text-muted-foreground hover:bg-accent/60 hover:text-foreground",
            )}
          />
        }
      >
        <CurrentIcon className="h-3.5 w-3.5" aria-hidden />
        <span>{t(`autoApprove.${current}.title`)}</span>
      </PopoverTrigger>
      <PopoverContent
        side="top"
        align="end"
        className="w-72 origin-(--transform-origin) p-1 duration-150 motion-reduce:animate-none"
      >
        <div className="px-2 pb-1 pt-1.5 text-xs font-medium text-muted-foreground">
          {t("autoApprove.menuTitle")}
        </div>
        <TooltipProvider>
          <div role="menu" aria-label={t("autoApprove.menuTitle")}>
            {MODES.map(({ mode, icon: Icon }) => {
              const selected = mode === current;
              return (
                <Tooltip key={mode}>
                  <TooltipTrigger
                    render={
                      <button
                        type="button"
                        role="menuitemradio"
                        aria-checked={selected}
                        onClick={() => select(mode)}
                        className={cn(
                          "flex w-full items-start gap-2.5 rounded-md px-2 py-2 text-left",
                          "transition-colors duration-150 motion-reduce:transition-none",
                          "focus:outline-none focus-visible:bg-accent hover:bg-accent/60",
                          selected && "bg-accent",
                        )}
                      />
                    }
                  >
                    <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium">
                        {t(`autoApprove.${mode}.title`)}
                      </span>
                      <span className="block text-xs text-muted-foreground">
                        {t(`autoApprove.${mode}.desc`)}
                      </span>
                    </span>
                    <Check
                      className={cn(
                        "mt-0.5 h-4 w-4 shrink-0",
                        selected ? "opacity-100" : "opacity-0",
                      )}
                      aria-hidden
                    />
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    <p className="max-w-xs">{t(`autoApprove.${mode}.tooltip`)}</p>
                  </TooltipContent>
                </Tooltip>
              );
            })}
          </div>
        </TooltipProvider>
        {/* Ask first with "Allow searches in this chat" on: say so, and let
            the user turn it off again. */}
        <div
          data-testid="searches-allowed"
          data-state={!auto && allowSearches ? "open" : "closed"}
          inert={auto || !allowSearches}
          className="grid grid-rows-[0fr] opacity-0 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=open]:grid-rows-[1fr] data-[state=open]:opacity-100 motion-reduce:transition-none"
        >
          <div className="min-h-0 overflow-hidden">
            <p className="mx-2 mt-1 flex items-center gap-2 border-t border-border pt-2 pb-1 text-xs text-muted-foreground">
              <span className="min-w-0 flex-1">{t("autoApprove.searchesAllowed")}</span>
              <button
                type="button"
                onClick={() => void askSearchesAgain()}
                className="shrink-0 rounded-sm font-medium text-foreground underline-offset-2 hover:underline focus:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {t("autoApprove.askAgain")}
              </button>
            </p>
          </div>
        </div>
      </PopoverContent>
    </Popover>
  );
}
