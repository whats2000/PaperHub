import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Copy, Check, ExternalLink } from "lucide-react";

import { useVersionStore } from "@/store/version";
import { CHANGELOG, localizedHighlights } from "@/lib/changelog";

const UPDATE_COMMAND = "docker compose pull && docker compose up -d";

export function ChangelogModal() {
  const { t, i18n } = useTranslation("about");
  const open = useVersionStore((s) => s.changelogOpen);
  const close = useVersionStore((s) => s.closeChangelog);
  const info = useVersionStore((s) => s.info);
  const [copied, setCopied] = useState(false);

  if (!open) return null;

  const copy = () => {
    void navigator.clipboard.writeText(UPDATE_COMMAND);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={t("title")}
      onClick={close}
    >
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-card p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-base font-semibold">{t("title")}</h2>
          <button
            type="button"
            onClick={close}
            className="rounded px-2 py-1 text-sm text-muted-foreground hover:bg-accent"
          >
            {t("close")}
          </button>
        </div>

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
    </div>
  );
}
