import { Dialog } from "@base-ui/react/dialog";
import { Check, ExternalLink, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type { SettingsCredentials, SettingsField, SettingsModelCheck } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";
import { Button } from "../ui/button";
import { Select } from "../ui/select";
import { Switch } from "../ui/switch";

export function SettingsModal() {
  const { t } = useTranslation(["common", "settings"]);
  const { isOpen, config, error, restartPending, close, fetchConfig, save } =
    useSettingsStore();
  const modelOptions = useSettingsStore((s) => s.modelOptions);
  const readiness = useSettingsStore((s) => s.readiness);
  const readinessChecking = useSettingsStore((s) => s.readinessChecking);
  const [activeCat, setActiveCat] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen && !config) void fetchConfig();
  }, [isOpen, config, fetchConfig]);

  // Flatten per-provider model options into one de-duped autocomplete list for
  // the model-name fields. Best-effort: empty list just means no suggestions.
  const modelSuggestions = Array.from(
    new Set(Object.values(modelOptions?.options ?? {}).flat()),
  ).sort();
  // readiness only validates the two gate models — surface their missing-key
  // hint inline on the matching field.
  const modelChecks: Record<string, SettingsModelCheck | undefined> = {
    PAPERHUB_MODEL_SMALL: readiness?.models.small,
    PAPERHUB_MODEL_FLAGSHIP: readiness?.models.flagship,
  };
  const renderField = (f: SettingsField) => (
    <FieldRow
      key={`${f.key}:${f.value ?? ""}`}
      field={f}
      onSave={save}
      modelListId={f.key.includes("MODEL") && modelSuggestions.length > 0 ? "model-suggestions" : undefined}
      modelCheck={modelChecks[f.key]}
      modelChecking={readinessChecking && f.key in modelChecks}
    />
  );

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
        {config ? (
          <>
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
              {t(`settings:category.${c.key}`, c.label)}
            </button>
          ))}
        </nav>
        {/* Field panel */}
        <div className="flex min-w-0 flex-1 flex-col">
          <div className="flex items-center justify-end border-b p-3">
            <Button
              variant="ghost"
              size="icon"
              aria-label={t("common:close")}
              title={t("common:close")}
              onClick={close}
            >
              <X />
            </Button>
          </div>
          {restartPending.length > 0 && (
            <div className="border-b bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950 dark:text-amber-200">
              {t("settings:restartPending", "Restart the backend to apply: {{keys}}", {
                keys: restartPending.join(", "),
              })}
            </div>
          )}
          <div className="flex-1 overflow-y-auto p-3">
            {current?.credentials && (
              <div className="mb-6">
                <CredentialEditor credentials={current.credentials} onSave={save} />
              </div>
            )}
            {current?.fields.filter((f) => !f.advanced).map(renderField)}
            {current?.fields.some((f) => f.advanced) && (
              <details className="mt-2 rounded-md border border-border">
                <summary className="cursor-pointer select-none px-3 py-2 text-sm font-medium text-muted-foreground">
                  {t("settings:advancedModels", "Per-slot model overrides")}
                </summary>
                <div className="border-t border-border p-3 pb-0">
                  {current.fields.filter((f) => f.advanced).map(renderField)}
                </div>
              </details>
            )}
            {/* Shared autocomplete source for all model-name fields. */}
            <datalist id="model-suggestions">
              {modelSuggestions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          </div>
        </div>
          </>
        ) : (
          <div className="flex flex-1 flex-col">
            <div className="flex items-center justify-between border-b p-3">
              <h2 id="settings-title" className="text-sm font-semibold">
                {t("common:settings")}
              </h2>
              <Button
                variant="ghost"
                size="icon"
                aria-label={t("common:close")}
                title={t("common:close")}
                onClick={close}
              >
                <X />
              </Button>
            </div>
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground">
              {error ? (
                <>
                  <p>{t("settings:loadFailed", "Couldn't load settings.")}</p>
                  <Button variant="outline" size="sm" onClick={() => void fetchConfig()}>
                    {t("settings:retry", "Retry")}
                  </Button>
                </>
              ) : (
                <Loader2 className="size-5 animate-spin" />
              )}
            </div>
          </div>
        )}
          </Dialog.Popup>
        </div>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function FieldRow({
  field,
  onSave,
  modelListId,
  modelCheck,
  modelChecking,
}: {
  field: SettingsField;
  onSave: (patch: Record<string, string | null>) => Promise<void>;
  /** When set, the string input offers this `<datalist>` of model suggestions. */
  modelListId?: string;
  /** Gate-model validity (small/flagship only) for an inline missing-key hint. */
  modelCheck?: SettingsModelCheck;
  /** True while readiness is being re-checked — show "checking…" not the stale
   *  warning (the pre-flight ping takes a few seconds). */
  modelChecking?: boolean;
}) {
  const { t } = useTranslation(["common", "settings"]);
  const [draft, setDraft] = useState<string>(field.value ?? "");
  const [replacing, setReplacing] = useState(false);

  // Localize label/help by key; the backend-provided English is the fallback.
  const label = t(`settings:field.${field.key}.label`, field.label);
  const helpText = t(`settings:field.${field.key}.help`, field.help ?? "");

  if (field.read_only) {
    return (
      <div className="mb-4">
        <label htmlFor={field.key} className="text-sm font-medium">
          {label}
        </label>
        <input
          id={field.key}
          readOnly
          value={field.value ?? ""}
          className="mt-1 w-full rounded border bg-muted px-2 py-1 text-sm"
        />
        {helpText && <p className="mt-1 text-xs text-muted-foreground">{helpText}</p>}
      </div>
    );
  }

  if (field.secret) {
    return (
      <div className="mb-4">
        <label htmlFor={field.key} className="text-sm font-medium">
          {label} {field.restart_required && <RestartBadge />}
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
            <Button
              size="icon"
              aria-label={t("save")}
              title={t("save")}
              onClick={() =>
                void onSave({ [field.key]: draft })
                  .then(() => setReplacing(false))
                  .catch((e: unknown) =>
                    toast.error(
                      e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
                    ),
                  )
              }
            >
              <Check />
            </Button>
          </div>
        ) : (
          <div className="mt-1 flex items-center gap-2 text-sm">
            <span className={field.is_set ? "text-green-600" : "text-muted-foreground"}>
              {field.is_set ? t("setIndicator", "••• set") : t("notSet", "not set")}
            </span>
            <Button
              variant="outline"
              size="icon-xs"
              aria-label={t("replace", "Replace")}
              title={t("replace", "Replace")}
              onClick={() => setReplacing(true)}
            >
              <Pencil />
            </Button>
          </div>
        )}
        {helpText && <p className="mt-1 text-xs text-muted-foreground">{helpText}</p>}
        {field.docs_url && <DocsLink url={field.docs_url} />}
      </div>
    );
  }

  // bool renders as an immediate-save Switch (toggles persist on change).
  if (field.type === "bool") {
    return (
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="min-w-0">
          <label htmlFor={field.key} className="text-sm font-medium">
            {label} {field.restart_required && <RestartBadge />}
          </label>
          {helpText && <p className="mt-1 text-xs text-muted-foreground">{helpText}</p>}
        </div>
        <Switch
          id={field.key}
          checked={field.value === "1"}
          onCheckedChange={(checked) =>
            void onSave({ [field.key]: checked ? "1" : "0" }).catch((e: unknown) =>
              toast.error(
                e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
              ),
            )
          }
        />
      </div>
    );
  }

  // string / int / email / enum
  return (
    <div className="mb-4">
      <label htmlFor={field.key} className="text-sm font-medium">
        {label} {field.restart_required && <RestartBadge />}
      </label>
      <div className="mt-1 flex gap-2">
        {field.type === "enum" ? (
          <Select
            id={field.key}
            value={draft}
            onValueChange={setDraft}
            options={(field.choices ?? []).map((c) => ({ value: c, label: c }))}
            className="w-full"
          />
        ) : (
          <input
            id={field.key}
            type={field.type === "int" ? "number" : "text"}
            list={modelListId}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            className="h-8 w-full rounded-md border border-border bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
          />
        )}
        <Button
          size="icon"
          aria-label={t("save")}
          title={t("save")}
          onClick={() =>
            void onSave({ [field.key]: draft === "" ? null : draft }).catch((e: unknown) =>
              toast.error(
                e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
              ),
            )
          }
        >
          <Check />
        </Button>
      </div>
      {modelChecking ? (
        <p className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
          <Loader2 className="size-3 animate-spin" />
          {t("settings:modelChecking", "Checking model availability…")}
        </p>
      ) : (
        modelCheck && !modelCheck.key_ok && (
          <p className="mt-1 text-xs text-destructive">
            {modelCheck.missing_keys.length > 0
              ? t("settings:modelKeyMissing", "Missing provider key: {{keys}}", {
                  keys: modelCheck.missing_keys.join(", "),
                })
              : t(
                  "settings:modelUnusable",
                  "This model isn't usable — check that the model name is available and the API key is valid.",
                )}
            {/* The provider's own reason (redacted) — distinguishes a rejected
                key from a wrong/unavailable model name, per slot. */}
            {modelCheck.detail && (
              <span className="mt-0.5 block font-normal text-muted-foreground">
                {modelCheck.detail}
              </span>
            )}
          </p>
        )
      )}
      {helpText && <p className="mt-1 text-xs text-muted-foreground">{helpText}</p>}
      {field.docs_url && <DocsLink url={field.docs_url} />}
    </div>
  );
}

function DocsLink({ url }: { url: string }) {
  const { t } = useTranslation("settings");
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className="mt-1 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
    >
      {t("getApiKey", "Get an API key")}
      <ExternalLink className="size-3" />
    </a>
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
  credentials,
  onSave,
}: {
  credentials: SettingsCredentials;
  onSave: (patch: Record<string, string | null>) => Promise<void>;
}) {
  const { t } = useTranslation(["settings", "common"]);
  const [newKey, setNewKey] = useState("");
  const [newVal, setNewVal] = useState("");
  return (
    <div>
      <div className="mb-3">
        <h3 className="text-sm font-semibold">
          {t("credentialsHeading", "Provider credentials")}
        </h3>
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          {t(
            "credentialsIntro",
            "PaperHub calls LLM providers through LiteLLM using your own API keys. Add a key as a name/value pair — the name is the provider's environment variable (e.g. OPENAI_API_KEY) and the value is the secret. Values are stored locally on this server and shown masked.",
          )}
        </p>
        <a
          href="https://docs.litellm.ai/docs/providers"
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
        >
          {t(
            "credentialsDocsLink",
            "See LiteLLM's provider list for the exact key name and where to get each key",
          )}
          <ExternalLink className="size-3" />
        </a>
      </div>
      <ul className="mb-4 space-y-1">
        {credentials.keys.map((k) => (
          <li
            key={k.key}
            className="flex items-center justify-between rounded-md border border-border px-2 py-1 text-sm"
          >
            <span className="truncate font-mono">{k.key}</span>
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={t("remove", "Remove")}
              title={t("remove", "Remove")}
              className="text-destructive hover:text-destructive"
              onClick={() =>
                void onSave({ [k.key]: null }).catch((e: unknown) =>
                  toast.error(
                    e instanceof Error ? e.message : t("settings:saveFailed", "Couldn't save the setting"),
                  ),
                )
              }
            >
              <Trash2 />
            </Button>
          </li>
        ))}
      </ul>
      <div className="flex items-end gap-2">
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor="cred-new-key" className="text-xs font-medium text-muted-foreground">
            {t("keyNameLabel", "Key name")}
          </label>
          <input
            id="cred-new-key"
            list="cred-suggestions"
            placeholder={t("providerKeyPlaceholder", "PROVIDER_API_KEY")}
            value={newKey}
            onChange={(e) => setNewKey(e.target.value.toUpperCase())}
            className="h-8 w-full rounded-md border border-border bg-background px-2.5 font-mono text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
          />
          <datalist id="cred-suggestions">
            {credentials.suggestions.map((s) => (
              <option key={s} value={s} />
            ))}
          </datalist>
        </div>
        <div className="flex flex-1 flex-col gap-1">
          <label htmlFor="cred-new-val" className="text-xs font-medium text-muted-foreground">
            {t("valueLabel", "Value")}
          </label>
          <input
            id="cred-new-val"
            type="password"
            placeholder={t("valuePlaceholder", "value")}
            value={newVal}
            onChange={(e) => setNewVal(e.target.value)}
            className="h-8 w-full rounded-md border border-border bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50"
          />
        </div>
        <Button
          size="icon"
          disabled={!newKey || !newVal}
          aria-label={t("add", "Add")}
          title={t("add", "Add")}
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
        >
          <Plus />
        </Button>
      </div>
    </div>
  );
}
