import { Dialog } from "@base-ui/react/dialog";
import { CheckCircle2, Circle, ExternalLink, Settings, Sparkles } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";

import { useSettingsStore } from "../../store/settings";
import { Button } from "../ui/button";

/** First-run onboarding tour. Auto-shows while the backend reports the app is
 *  not ready (no usable small/flagship model) and the user hasn't dismissed it.
 *  The composer stays locked regardless — this modal is guidance, not the gate. */
export function WelcomeModal() {
  const { t } = useTranslation(["settings", "common"]);
  const readiness = useSettingsStore((s) => s.readiness);
  const welcomeDismissed = useSettingsStore((s) => s.welcomeDismissed);
  const fetchReadiness = useSettingsStore((s) => s.fetchReadiness);
  const dismissWelcome = useSettingsStore((s) => s.dismissWelcome);
  const openSettings = useSettingsStore((s) => s.open);

  // Pull the gate state once on mount (App boot also triggers this, but mounting
  // the modal independently keeps it self-sufficient in tests).
  useEffect(() => {
    if (readiness === null) void fetchReadiness();
  }, [readiness, fetchReadiness]);

  const open = readiness !== null && !readiness.ready && !welcomeDismissed;

  const smallOk = readiness?.models.small.key_ok ?? false;
  const flagshipOk = readiness?.models.flagship.key_ok ?? false;
  const steps = [
    // A working model proves the key works; a removed/empty key fails the ping,
    // so the credential step reflects "a usable key", not just a DB row.
    { key: "credential", done: (readiness?.credentials_set ?? false) || smallOk || flagshipOk },
    { key: "small", done: smallOk },
    { key: "flagship", done: flagshipOk },
  ];

  return (
    <Dialog.Root open={open} onOpenChange={(o) => { if (!o) dismissWelcome(); }}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/40 transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center p-4">
          <Dialog.Popup
            aria-labelledby="welcome-title"
            className="pointer-events-auto w-[460px] max-w-[92vw] overflow-hidden rounded-lg border bg-background p-6 shadow-xl transition-all duration-200 data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0"
          >
            <div className="mb-1 flex items-center gap-2">
              <Sparkles className="size-5 text-primary" />
              <h2 id="welcome-title" className="text-base font-semibold">
                {t("welcome.title", "Welcome to PaperHub")}
              </h2>
            </div>
            <p className="mb-4 text-sm text-muted-foreground">
              {t(
                "welcome.subtitle",
                "Connect a model provider to start chatting. Finish these steps in Settings:",
              )}
            </p>
            <ul className="mb-5 space-y-3">
              {steps.map((step) => (
                <li key={step.key} className="flex items-start gap-2.5">
                  {step.done ? (
                    <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-green-600" />
                  ) : (
                    <Circle className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0">
                    <p
                      className={`text-sm font-medium ${step.done ? "text-muted-foreground line-through" : ""}`}
                    >
                      {t(`welcome.step.${step.key}.label`, step.key)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {t(`welcome.step.${step.key}.help`, "")}
                    </p>
                  </div>
                </li>
              ))}
            </ul>
            <p className="mb-2 text-xs text-muted-foreground">
              {t(
                "welcome.lockNote",
                "The composer stays locked until all three are valid.",
              )}
            </p>
            <p className="mb-4 text-xs text-muted-foreground">
              {t(
                "welcome.optionalSS",
                "Optional: add a Semantic Scholar API key in Settings → Integrations for faster paper search (the free tier is rate-limited; it won't block anything).",
              )}{" "}
              <a
                href="https://www.semanticscholar.org/product/api#api-key"
                target="_blank"
                rel="noopener noreferrer"
                className="font-medium text-primary hover:underline"
              >
                {t("welcome.optionalSSLink", "Get a key")}
                <ExternalLink className="ml-0.5 inline size-3 align-text-bottom" />
              </a>
            </p>
            <div className="flex justify-end gap-2">
              <Button variant="ghost" size="sm" onClick={dismissWelcome}>
                {t("welcome.later", "Maybe later")}
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  dismissWelcome();
                  openSettings();
                }}
              >
                <Settings className="size-4" />
                {t("welcome.openSettings", "Open Settings")}
              </Button>
            </div>
          </Dialog.Popup>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
