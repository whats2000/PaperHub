import { Dialog } from "@base-ui/react/dialog";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type { SettingsField } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";

export function SettingsModal() {
  const { t } = useTranslation(["common", "settings"]);
  const { isOpen, config, restartPending, close, fetchConfig, save } = useSettingsStore();
  const [activeCat, setActiveCat] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && !config) void fetchConfig();
  }, [isOpen, config, fetchConfig]);

  // Derive the effective category: an explicit selection, else the first one.
  // Deriving (rather than syncing via an effect) avoids cascading renders.
  const effectiveCat = activeCat ?? config?.categories[0]?.key ?? null;
  const current = config?.categories.find((c) => c.key === effectiveCat);

  // Base UI Dialog keeps the popup mounted through the close transition, so both
  // open and close animate (fade + scale). The centering wrapper isolates the
  // scale transform from the centering transform so they don't fight.
  return (
    <Dialog.Root
      open={isOpen}
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-black/40 transition-opacity duration-200 data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <div className="pointer-events-none fixed inset-0 z-50 grid place-items-center p-4">
          <Dialog.Popup
            aria-labelledby="settings-title"
            className="pointer-events-auto flex h-[70vh] w-[840px] max-w-[92vw] overflow-hidden rounded-lg border bg-background shadow-xl transition-all duration-200 data-[ending-style]:scale-95 data-[ending-style]:opacity-0 data-[starting-style]:scale-95 data-[starting-style]:opacity-0"
          >
        {/* Left nav */}
        <nav className="w-56 shrink-0 overflow-y-auto border-r p-2">
          <h2 id="settings-title" className="px-2 py-1 text-sm font-semibold">
            {t("common:settings")}
          </h2>
          {config?.categories.map((c) => (
            <button
              key={c.key}
              onClick={() => setActiveCat(c.key)}
              className={`block w-full rounded px-2 py-1.5 text-left text-sm ${
                c.key === effectiveCat ? "bg-muted font-medium" : "hover:bg-muted/60"
              }`}
            >
              {c.label}
            </button>
          ))}
        </nav>
        {/* Field panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-end border-b p-3">
            <button onClick={close} className="rounded px-2 py-1 text-sm hover:bg-muted">
              {t("common:close")}
            </button>
          </div>
          {restartPending.length > 0 && (
            <div className="border-b bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
              {t("settings:restartPending", "Restart the backend to apply: {{keys}}", {
                keys: restartPending.join(", "),
              })}
            </div>
          )}
          <div className="flex-1 overflow-y-auto p-3">
            {current?.free_form ? (
              <CredentialEditor
                suggestions={current.suggestions}
                fields={current.fields}
                onSave={save}
              />
            ) : (
              current?.fields.map((f) => (
                <FieldRow key={`${f.key}:${f.value ?? ""}`} field={f} onSave={save} />
              ))
            )}
          </div>
        </div>
          </Dialog.Popup>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function FieldRow({
  field,
  onSave,
}: {
  field: SettingsField;
  onSave: (patch: Record<string, string | null>) => Promise<void>;
}) {
  const { t } = useTranslation(["common", "settings"]);
  const [draft, setDraft] = useState<string>(field.value ?? "");
  const [replacing, setReplacing] = useState(false);

  if (field.read_only) {
    return (
      <div className="mb-4">
        <label htmlFor={field.key} className="text-sm font-medium">
          {field.label}
        </label>
        <input
          id={field.key}
          readOnly
          value={field.value ?? ""}
          className="mt-1 w-full rounded border bg-muted px-2 py-1 text-sm"
        />
        {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
      </div>
    );
  }

  if (field.secret) {
    return (
      <div className="mb-4">
        <label htmlFor={field.key} className="text-sm font-medium">
          {field.label} {field.restart_required && <RestartBadge />}
        </label>
        {replacing ? (
          <div className="mt-1 flex gap-2">
            <input
              id={field.key}
              type="password"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              className="w-full rounded border px-2 py-1 text-sm"
            />
            <button
              onClick={() =>
                void onSave({ [field.key]: draft })
                  .then(() => setReplacing(false))
                  .catch((e: unknown) =>
                    toast.error(
                      e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
                    ),
                  )
              }
              className="rounded bg-primary px-3 text-sm text-primary-foreground"
            >
              {t("save")}
            </button>
          </div>
        ) : (
          <div className="mt-1 flex items-center gap-2 text-sm">
            <span className={field.is_set ? "text-green-600" : "text-muted-foreground"}>
              {field.is_set ? t("setIndicator", "••• set") : t("notSet", "not set")}
            </span>
            <button onClick={() => setReplacing(true)} className="rounded border px-2 py-0.5 text-xs">
              {t("replace", "Replace")}
            </button>
          </div>
        )}
        {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
      </div>
    );
  }

  // string / int / email / enum / bool
  return (
    <div className="mb-4">
      <label htmlFor={field.key} className="text-sm font-medium">
        {field.label} {field.restart_required && <RestartBadge />}
      </label>
      <div className="mt-1 flex gap-2">
        {field.type === "enum" ? (
          <select
            id={field.key}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded border px-2 py-1 text-sm"
          >
            {field.choices?.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        ) : field.type === "bool" ? (
          <select
            id={field.key}
            value={draft || "0"}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded border px-2 py-1 text-sm"
          >
            <option value="1">on</option>
            <option value="0">off</option>
          </select>
        ) : (
          <input
            id={field.key}
            type={field.type === "int" ? "number" : "text"}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="w-full rounded border px-2 py-1 text-sm"
          />
        )}
        <button
          onClick={() =>
            void onSave({ [field.key]: draft === "" ? null : draft }).catch((e: unknown) =>
              toast.error(
                e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
              ),
            )
          }
          className="rounded bg-primary px-3 text-sm text-primary-foreground"
        >
          {t("save")}
        </button>
      </div>
      {field.help && <p className="mt-1 text-xs text-muted-foreground">{field.help}</p>}
    </div>
  );
}

function RestartBadge() {
  const { t } = useTranslation("settings");
  return (
    <span className="ml-1 rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-medium text-amber-800 dark:bg-amber-900 dark:text-amber-200">
      {t("restartBadge", "Restart to apply")}
    </span>
  );
}

function CredentialEditor({
  suggestions,
  fields,
  onSave,
}: {
  suggestions: string[];
  fields: SettingsField[];
  onSave: (patch: Record<string, string | null>) => Promise<void>;
}) {
  const { t } = useTranslation(["settings", "common"]);
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  return (
    <div>
      <ul className="mb-4 space-y-1">
        {fields.map((f) => (
          <li
            key={f.key}
            className="flex items-center justify-between rounded border px-2 py-1 text-sm"
          >
            <span className="font-mono">{f.key}</span>
            <button
              onClick={() =>
                void onSave({ [f.key]: null }).catch((e: unknown) =>
                  toast.error(
                    e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
                  ),
                )
              }
              className="text-xs text-red-600"
            >
              {t("remove", "Remove")}
            </button>
          </li>
        ))}
      </ul>
      <div className="flex gap-2">
        <input
          list="cred-suggestions"
          aria-label={t("providerKeyPlaceholder", "PROVIDER_API_KEY")}
          placeholder={t("providerKeyPlaceholder", "PROVIDER_API_KEY")}
          value={newKey}
          onChange={(e) => setNewKey(e.target.value.toUpperCase())}
          className="w-1/2 rounded border px-2 py-1 font-mono text-sm"
        />
        <datalist id="cred-suggestions">
          {suggestions.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        <input
          type="password"
          aria-label={t("valuePlaceholder", "value")}
          placeholder={t("valuePlaceholder", "value")}
          value={newVal}
          onChange={(e) => setNewVal(e.target.value)}
          className="w-1/2 rounded border px-2 py-1 text-sm"
        />
        <button
          disabled={!newKey || !newVal}
          onClick={() =>
            void onSave({ [newKey]: newVal })
              .then(() => {
                setNewKey("");
                setNewVal("");
              })
              .catch((e: unknown) =>
                toast.error(
                  e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
                ),
              )
          }
          className="rounded bg-primary px-3 text-sm text-primary-foreground disabled:opacity-50"
        >
          {t("add", "Add")}
        </button>
      </div>
    </div>
  );
}
