import { Dialog } from "@base-ui/react/dialog";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check, ExternalLink, X } from "lucide-react";

import { useVersionStore } from "@/store/version";
import { CHANGELOG, localizedHighlights } from "@/lib/changelog";

// PaperHub's compose file builds from source (no registry images for the
// self-hosted install), so the upgrade path is: refresh the source, then
// rebuild + restart — matching the README's `docker compose up -d --build`.
const UPDATE_COMMAND = "git pull && docker compose up -d --build";

export function ChangelogModal() {
  const { t, i18n } = useTranslation("about");
  const open = useVersionStore((s) => s.changelogOpen);
  const close = useVersionStore((s) => s.closeChangelog);
  const info = useVersionStore((s) => s.info);
  const [copied, setCopied] = useState(false);

  const copy = () => {
    // Only flip to "Copied" when the write actually succeeds — on an insecure
    // context / denied permission the command stays visible for manual copy.
    void navigator.clipboard.writeText(UPDATE_COMMAND).then(
      () => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      },
      () => undefined,
    );
  };

  // Base UI Dialog keeps the popup mounted through the close transition, so both
  // open and close animate (fade + scale) — consistent with SettingsModal. The
  // centering wrapper isolates the scale transform from the centering transform.
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/40 transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center p-4">
          <Dialog.Popup
            aria-labelledby="changelog-title"
            className="pointer-events-auto flex max-h-[80vh] w-full max-w-lg flex-col overflow-hidden rounded-lg border border-border bg-card shadow-xl transition-all duration-200 data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0"
          >
            <div className="flex items-center justify-between border-b border-border p-4">
              <h2 id="changelog-title" className="text-base font-semibold">
                {t("title")}
              </h2>
              <button
                type="button"
                onClick={close}
                aria-label={t("close")}
                title={t("close")}
                className="rounded p-1 text-muted-foreground hover:bg-accent"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="overflow-y-auto p-5">
              {info && (
                <p className="mb-3 text-xs text-muted-foreground">
                  {t("currentVersion", { version: info.current })}
                </p>
              )}

              {info?.update_available && info.latest && (
                <div className="mb-4 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm dark:border-amber-800 dark:bg-amber-950">
                  <p className="font-medium text-amber-800 dark:text-amber-200">
                    {t("updateAvailable", { version: info.latest })}
                  </p>
                  <p className="mt-2 text-xs text-amber-800 dark:text-amber-200">{t("updateHint")}</p>
                  <div className="mt-1 flex items-center gap-2">
                    <code className="flex-1 truncate rounded bg-background px-2 py-1 text-xs">
                      {UPDATE_COMMAND}
                    </code>
                    <button
                      type="button"
                      onClick={copy}
                      aria-label={t("copy")}
                      className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-xs hover:bg-accent"
                    >
                      {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
                      {copied ? t("copied") : t("copy")}
                    </button>
                  </div>
                  {info.html_url && (
                    <a
                      href={info.html_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-2 inline-flex items-center gap-1 text-xs underline"
                    >
                      <ExternalLink className="h-3 w-3" />
                      {t("viewRelease")}
                    </a>
                  )}
                </div>
              )}

              <ul className="space-y-4">
                {CHANGELOG.map((entry) => (
                  <li key={entry.version}>
                    <div className="flex items-baseline justify-between">
                      <span className="text-sm font-semibold">v{entry.version}</span>
                      <span className="text-xs text-muted-foreground">{entry.date}</span>
                    </div>
                    <ul className="mt-1 list-disc space-y-1 pl-5 text-sm text-foreground/90">
                      {localizedHighlights(entry, i18n.language).map((h, i) => (
                        <li key={i}>{h}</li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            </div>
          </Dialog.Popup>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
