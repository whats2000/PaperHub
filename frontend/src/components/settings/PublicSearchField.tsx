import { Lock, TriangleAlert } from "lucide-react";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "../ui/button";
import { Switch } from "../ui/switch";

const COLLAPSE =
  "grid grid-rows-[1fr] opacity-100 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none";

/**
 * "Public web search": an opt-in the user must confirm. Switching it on only
 * expands an amber warning; nothing is saved until "Turn on public search".
 * Turning it off saves at once.
 */
export function PublicSearchField({
  id,
  label,
  help,
  checked,
  locked = false,
  turnedOnAt,
  onSave,
}: {
  id: string;
  label: string;
  help?: string;
  checked: boolean;
  locked?: boolean;
  /** When it was turned on (settings row updated_at, UTC); omitted when unknown. */
  turnedOnAt?: string | null;
  /** Persist the setting ("1" / "0"). */
  onSave: (value: "1" | "0") => Promise<void> | void;
}) {
  const { t, i18n } = useTranslation("settings");
  const when = formatWhen(turnedOnAt, i18n.language);
  const [confirming, setConfirming] = useState(false);
  const switchRef = useRef<HTMLButtonElement>(null);
  const warnId = `${id}-warn`;

  return (
    <div className="mb-4">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <label htmlFor={id} className="text-sm font-medium">
            {label}
          </label>
          {help && <p className="mt-1 text-xs text-muted-foreground">{help}</p>}
          {locked && <LockNote />}
        </div>
        <Switch
          id={id}
          ref={switchRef}
          checked={checked}
          disabled={locked}
          aria-controls={warnId}
          onCheckedChange={(next) => {
            if (next) setConfirming(true);
            else {
              setConfirming(false);
              void onSave("0");
            }
          }}
        />
      </div>

      <div
        data-testid="public-search-warning"
        data-state={confirming && !checked ? "open" : "closed"}
        inert={!(confirming && !checked)}
        className={COLLAPSE}
      >
        <div className="min-h-0 overflow-hidden">
          <div
            id={warnId}
            role="region"
            aria-labelledby={`${warnId}-title`}
            className="mt-2.5 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2.5 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200"
          >
            <b id={`${warnId}-title`}>
              {t("privacy.publicWarnTitle", "Your search text will leave this device.")}
            </b>
            <p className="mt-1">
              {t(
                "privacy.publicWarnBody",
                "Queries go to public search engines outside your organisation. Each one is still reviewed first, but you take the risk of what a query reveals about your work.",
              )}
            </p>
            <div className="mt-2 flex gap-2">
              <Button
                size="sm"
                onClick={() => {
                  setConfirming(false);
                  void onSave("1");
                }}
              >
                {t("privacy.publicTurnOn", "Turn on public search")}
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  setConfirming(false);
                  switchRef.current?.focus();
                }}
              >
                {t("privacy.publicKeepOff", "Keep it off")}
              </Button>
            </div>
          </div>
        </div>
      </div>

      <div
        data-testid="public-search-on"
        data-state={checked ? "open" : "closed"}
        inert={!checked}
        className={COLLAPSE}
      >
        <div className="min-h-0 overflow-hidden">
          <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
            <TriangleAlert className="size-3 shrink-0" />
            {when
              ? t(
                  "privacy.publicOnNoteBy",
                  "On. Queries reach public engines after review. Turned on by you, {{when}}.",
                  { when },
                )
              : t("privacy.publicOnNote", "On. Queries reach public engines after review.")}
          </p>
        </div>
      </div>
    </div>
  );
}

/** Dashed "Set by the deployer" note shown under a locked setting. */
export function LockNote() {
  const { t } = useTranslation("settings");
  return (
    <span className="mt-1 inline-flex items-center gap-1 rounded-md border border-dashed border-border px-1.5 py-px text-xs text-muted-foreground">
      <Lock className="size-3" />
      {t("privacy.lockedByDeployer", "Set by the deployer")}
    </span>
  );
}

/** SQLite stores UTC as "YYYY-MM-DD HH:MM:SS"; show it in the viewer's locale. */
function formatWhen(raw: string | null | undefined, locale: string): string | null {
  if (!raw) return null;
  const d = new Date(raw.includes("T") ? raw : raw.replace(" ", "T") + "Z");
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" });
}
