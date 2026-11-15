import { ChevronDown, CircleCheck, Globe, Lock, ScrollText, TriangleAlert } from "lucide-react";
import { Fragment, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import type {
  OutboundDestination,
  SettingsField,
  SettingsReadiness,
  TrustedSearchStatus,
} from "../../lib/api";
import { cn } from "../../lib/utils";
import { Switch } from "../ui/switch";
import { AdvancedSection } from "./AdvancedSection";
import { LockNote, PublicSearchField } from "./PublicSearchField";
import { StringListField } from "./StringListField";

export const PRIVACY_CATEGORY = "privacy_outbound";

type Save = (patch: Record<string, string | null>) => Promise<void>;

const COLLAPSE =
  "grid grid-rows-[1fr] opacity-100 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none";

/** Header pill: how many of this pane's settings the deployer pinned. */
export function LockedCountPill({ fields }: { fields: SettingsField[] }) {
  const { t } = useTranslation("settings");
  const n = fields.filter((f) => f.locked).length;
  if (n === 0) return null;
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-xs text-muted-foreground">
      <Lock className="size-3" />
      {t("privacy.lockedCount", "{{count}} set by the deployer", { count: n })}
    </span>
  );
}

/** A cloud-model setting's display name; `env` marks a server-environment key. */
export interface NonLocalLabel {
  label: string;
  /** Not a panel setting: set only in the server environment. */
  env: boolean;
}

/**
 * Amber notice at the top of the privacy pane body: how many settings point
 * at a cloud model, with a "Show which" list grouped by model. Warns, never
 * refuses. Keeps the last list while collapsing so it can exit.
 */
export function NonLocalBanner({
  readiness,
  labelFor,
}: {
  readiness: SettingsReadiness | null;
  labelFor: (key: string) => NonLocalLabel;
}) {
  const { t } = useTranslation("settings");
  const incoming = readiness?.non_local ?? [];
  const [shown, setShown] = useState(incoming);
  const [expanded, setExpanded] = useState(false);
  if (incoming.length > 0 && incoming !== shown) setShown(incoming);
  const open = incoming.length > 0;
  const name = (key: string) => {
    const info = labelFor(key);
    return info.env
      ? t("privacy.serverEnv", "{{name}} (server environment)", { name: info.label })
      : info.label;
  };
  // Group by model, in the order each model first appears.
  const byModel = new Map<string, string[]>();
  for (const n of shown) {
    const names = byModel.get(n.model) ?? [];
    names.push(name(n.key));
    byModel.set(n.model, names);
  }
  return (
    <div data-state={open ? "open" : "closed"} inert={!open} className={COLLAPSE}>
      <div className="min-h-0 overflow-hidden">
        <div
          role="status"
          className="mb-3 rounded-md border border-amber-300 bg-amber-50/60 px-2.5 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200"
        >
          <div className="flex items-center gap-2">
            <TriangleAlert className="size-3.5 shrink-0" aria-hidden="true" />
            <b className="min-w-0 flex-1 font-medium">
              {shown.length === 1
                ? t(
                    "privacy.nonLocalOne",
                    "1 setting uses a cloud model, so some text may leave this device.",
                  )
                : t(
                    "privacy.nonLocalMany",
                    "{{count}} settings use a cloud model, so some text may leave this device.",
                    { count: shown.length },
                  )}
            </b>
            <button
              type="button"
              aria-expanded={expanded}
              aria-controls="cloud-model-list"
              onClick={() => setExpanded((v) => !v)}
              className="inline-flex shrink-0 items-center gap-0.5 whitespace-nowrap underline underline-offset-2"
            >
              {expanded ? t("privacy.cloudHide", "Hide") : t("privacy.cloudShow", "Show which")}
              <ChevronDown
                aria-hidden="true"
                className={cn(
                  "size-3.5 transition-transform duration-200 ease-out motion-reduce:transition-none",
                  expanded && "rotate-180",
                )}
              />
            </button>
          </div>
          <div data-state={expanded ? "open" : "closed"} inert={!expanded} className={COLLAPSE}>
            <div className="min-h-0 overflow-hidden">
              <dl
                id="cloud-model-list"
                data-testid="cloud-model-list"
                className="mt-2 grid grid-cols-[max-content_1fr] gap-x-3 gap-y-1"
              >
                {[...byModel].map(([model, names]) => (
                  <Fragment key={model}>
                    <dt>
                      <code className="font-mono text-[11.5px]">{model}</code>
                    </dt>
                    <dd className="min-w-0 text-foreground/85">
                      {t("privacy.cloudModelSettings", "{{count}} settings: {{names}}", {
                        count: names.length,
                        names: names.join(", "),
                      })}
                    </dd>
                  </Fragment>
                ))}
              </dl>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Group({ title, intro, children }: { title: string; intro: string; children: ReactNode }) {
  return (
    <section className="mt-4 overflow-hidden rounded-xl border border-border first:mt-0">
      <header className="border-b border-border bg-muted/40 px-4 py-2.5">
        <h3 className="text-sm font-semibold text-foreground">{title}</h3>
        <p className="mt-0.5 text-xs text-muted-foreground">{intro}</p>
      </header>
      <div className="px-4 pt-4">{children}</div>
    </section>
  );
}

function SwitchRow({
  field,
  label,
  help,
  save,
}: {
  field: SettingsField;
  label: string;
  help: string;
  save: (patch: Record<string, string | null>) => void;
}) {
  const locked = !!field.locked;
  return (
    <div className="mb-4 flex items-center justify-between gap-3">
      <div className="min-w-0">
        <label htmlFor={field.key} className="text-sm font-medium">
          {label}
        </label>
        {help && <p className="mt-1 text-xs text-muted-foreground">{help}</p>}
        {locked && <LockNote />}
      </div>
      <Switch
        id={field.key}
        checked={field.value === "1"}
        disabled={locked}
        onCheckedChange={(c) => save({ [field.key]: c ? "1" : "0" })}
      />
    </div>
  );
}

function TextRow({
  field,
  label,
  help,
  save,
  type = "text",
  suffix,
  narrow,
  validateUrl,
  below,
}: {
  field: SettingsField;
  label: string;
  help: string;
  /** Resolves when saved; rejects (after toasting) when it failed. */
  save: (patch: Record<string, string | null>) => Promise<void>;
  type?: "text" | "number";
  suffix?: string;
  narrow?: boolean;
  /** Require an empty value or an http(s) URL. */
  validateUrl?: boolean;
  /** Extra line under the input (e.g. the reachability note). */
  below?: ReactNode;
}) {
  const { t } = useTranslation("settings");
  const locked = !!field.locked;
  const saved = field.value ?? "";
  const [draft, setDraft] = useState(saved);
  const [invalid, setInvalid] = useState<string | null>(null);
  const commit = () => {
    if (locked || draft === saved) return;
    if (validateUrl && draft.trim() !== "") {
      let ok: boolean;
      try {
        const u = new URL(draft.trim());
        ok = u.protocol === "http:" || u.protocol === "https:";
      } catch {
        ok = false;
      }
      if (!ok) {
        setInvalid(
          t("privacy.invalidUrl", "Enter a full http:// or https:// address, or leave it empty."),
        );
        return;
      }
    }
    if (type === "number") {
      const n = Number(draft);
      const bad =
        !Number.isInteger(n) ||
        draft === "" ||
        (field.min != null && n < field.min) ||
        (field.max != null && n > field.max);
      if (bad) {
        setInvalid(t("privacy.invalidNumber", "Enter a whole number within the allowed range."));
        return;
      }
    }
    setInvalid(null);
    save({ [field.key]: draft.trim() === "" ? null : draft.trim() }).catch(() => setDraft(saved));
  };
  return (
    <div className="mb-4">
      <label htmlFor={field.key} className="text-sm font-medium">
        {label}
      </label>
      {help && <p className="mt-1 text-xs text-muted-foreground">{help}</p>}
      <div className="mt-1.5 flex items-center gap-2">
        <input
          id={field.key}
          type={type}
          min={field.min}
          max={field.max}
          disabled={locked}
          value={draft}
          aria-invalid={invalid ? true : undefined}
          onChange={(e) => {
            setDraft(e.target.value);
            setInvalid(null);
          }}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
          }}
          className={cn(
            "h-8 w-full rounded-md border border-border bg-background px-2.5 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-60",
            narrow && "max-w-40",
          )}
        />
        {suffix && <span className="text-xs text-muted-foreground">{suffix}</span>}
      </div>
      <div
        data-state={invalid ? "open" : "closed"}
        inert={!invalid}
        className={COLLAPSE}
      >
        <p role="alert" className="min-h-0 overflow-hidden pt-1 text-xs text-amber-700 dark:text-amber-300">
          {invalid}
        </p>
      </div>
      {below}
      {locked && <LockNote />}
    </div>
  );
}

/** Reachability note under the trusted search URL (green check / amber note). */
function TrustedStatusNote({ status }: { status: TrustedSearchStatus | null }) {
  const { t } = useTranslation("settings");
  // "not configured" has nothing to report; keep the last state while collapsing.
  const [shown, setShown] = useState(status);
  if (status && status.error !== "not_configured" && status !== shown) setShown(status);
  const open = !!status && status.error !== "not_configured";
  return (
    <div data-state={open ? "open" : "closed"} inert={!open} className={COLLAPSE}>
      <div className="min-h-0 overflow-hidden">
        {shown?.reachable ? (
          <span className="mt-1.5 inline-flex items-center gap-1 text-xs text-green-600">
            <CircleCheck className="size-3.5" />
            {t("privacy.trustedReachable", "Reachable")}
          </span>
        ) : (
          <span className="mt-1.5 inline-flex items-center gap-1 text-xs text-amber-700 dark:text-amber-300">
            <TriangleAlert className="size-3.5" />
            {t("privacy.trustedUnreachable", "Not reachable")}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * The "Privacy & outbound" settings pane (SRS §III-10.5): outbound review,
 * web search, allowed destinations and the outbound log retention.
 */
export function PrivacyPane({
  fields,
  save,
  destinations,
  trustedStatus,
  renderField,
  onOpenLog,
}: {
  fields: SettingsField[];
  save: Save;
  trustedStatus: TrustedSearchStatus | null;
  destinations: OutboundDestination[] | null;
  /** Generic row renderer, used for the advanced (model / timeout) fields. */
  renderField: (f: SettingsField) => ReactNode;
  /** Switches Settings to the Outbound log section. */
  onOpenLog?: () => void;
}) {
  const { t } = useTranslation("settings");
  const { t: tOut } = useTranslation("outbound");
  const by = (key: string) => fields.find((f) => f.key === key);
  const label = (f: SettingsField) => t(`settings:field.${f.key}.label`, f.label);
  const help = (f: SettingsField) => t(`settings:field.${f.key}.help`, f.help ?? "");

  // Toasts the failure and rethrows, so a caller can roll its UI back.
  const saveToast = (patch: Record<string, string | null>) =>
    save(patch).catch((e: unknown) => {
      const msg = e instanceof Error ? e.message : "";
      toast.error(
        msg.includes("403")
          ? t("privacy.lockedByDeployer", "Set by the deployer")
          : msg || t("settings:saveFailed", "Couldn't save the setting"),
      );
      throw e;
    });
  const fire = (patch: Record<string, string | null>) => {
    saveToast(patch).catch(() => undefined);
  };

  const review = by("PAPERHUB_OUTBOUND_REVIEW");
  const unsure = by("PAPERHUB_OUTBOUND_UNSURE");
  const terms = by("PAPERHUB_SENSITIVE_TERMS");
  const privDefault = by("PAPERHUB_PRIVATE_UPLOAD_DEFAULT");
  const trusted = by("PAPERHUB_TRUSTED_SEARCH_URL");
  const pub = by("PAPERHUB_PUBLIC_WEB_SEARCH");
  const allow = by("PAPERHUB_OUTBOUND_ALLOWLIST");
  const retention = by("PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS");
  const advanced = fields.filter(
    (f) => f.advanced && f.key.startsWith("PAPERHUB_OUTBOUND_"),
  );

  const sourceLabel: Record<OutboundDestination["source"], string> = {
    paper_source: t("privacy.srcPaperSource", "Paper source"),
    trusted_search: t("privacy.srcTrustedSearch", "Trusted search"),
    public_search: t("privacy.srcPublicSearch", "Public search"),
    deployer: t("privacy.srcDeployer", "Deployer"),
  };
  const unsureOptions: { value: string; text: string }[] = [
    { value: "ask", text: t("privacy.unsureAsk", "Ask me") },
    { value: "block", text: t("privacy.unsureBlock", "Don't send") },
  ];

  return (
    <>
      <Group
        title={t("privacy.groupReview", "Outbound review")}
        intro={t(
          "privacy.groupReviewIntro",
          "Every search or fetch that carries your text is checked before it leaves this device.",
        )}
      >
        {review && <SwitchRow field={review} label={label(review)} help={help(review)} save={fire} />}
        {unsure && (
          <div className="mb-4">
            <span id={unsure.key} className="text-sm font-medium">
              {label(unsure)}
            </span>
            {help(unsure) && <p className="mt-1 text-xs text-muted-foreground">{help(unsure)}</p>}
            <div
              role="group"
              aria-labelledby={unsure.key}
              className="mt-1.5 inline-flex overflow-hidden rounded-md border border-border"
            >
              {unsureOptions.map((o) => (
                <button
                  key={o.value}
                  type="button"
                  aria-pressed={unsure.value === o.value}
                  disabled={!!unsure.locked}
                  onClick={() => {
                    if (unsure.value !== o.value) fire({ [unsure.key]: o.value });
                  }}
                  className={cn(
                    "px-3 py-1 text-sm transition-colors disabled:cursor-not-allowed disabled:opacity-60",
                    unsure.value === o.value ? "bg-muted font-medium" : "hover:bg-muted/60",
                  )}
                >
                  {o.text}
                </button>
              ))}
            </div>
            {unsure.locked && (
              <div>
                <LockNote />
              </div>
            )}
          </div>
        )}
        {terms && (
          <StringListField
            id={terms.key}
            label={label(terms)}
            help={help(terms)}
            value={terms.value ?? ""}
            locked={!!terms.locked}
            placeholder={t("privacy.termPlaceholder", "Add a term, then Enter")}
            onChange={(joined) => saveToast({ [terms.key]: joined })}
          />
        )}
        {privDefault && (
          <SwitchRow field={privDefault} label={label(privDefault)} help={help(privDefault)} save={fire} />
        )}
      </Group>

      <Group
        title={t("privacy.groupSearch", "Web search")}
        intro={t(
          "privacy.groupSearchIntro",
          "Two ways to search the web. Both go through the outbound review.",
        )}
      >
        {trusted && (
          <TextRow
            key={`${trusted.key}:${trusted.value ?? ""}`}
            field={trusted}
            label={label(trusted)}
            help={help(trusted)}
            save={saveToast}
            validateUrl
            below={<TrustedStatusNote status={trustedStatus} />}
          />
        )}
        {pub && (
          <PublicSearchField
            id={pub.key}
            label={label(pub)}
            help={help(pub)}
            checked={pub.value === "1"}
            locked={!!pub.locked}
            turnedOnAt={pub.value === "1" ? pub.updated_at : null}
            onSave={(v) => saveToast({ [pub.key]: v })}
          />
        )}
      </Group>

      <Group
        title={t("privacy.groupDestinations", "Allowed destinations")}
        intro={t(
          "privacy.groupDestinationsIntro",
          "PaperHub only contacts these hosts. Anything else is blocked.",
        )}
      >
        <div className="mb-4">
          <ul className="space-y-1">
            {(destinations ?? []).map((d) => (
              <li
                key={`${d.host}:${d.source}`}
                className="flex items-center gap-2 rounded-md border border-border px-2 py-1 text-sm animate-in fade-in-0 motion-reduce:animate-none"
              >
                <Globe className="size-3.5 shrink-0 text-muted-foreground" />
                <span className="truncate">
                  {d.host === "*" ? t("privacy.anyHost", "Any host") : d.host}
                </span>
                <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                  {sourceLabel[d.source]}
                </span>
              </li>
            ))}
          </ul>
          {allow?.locked && <LockNote />}
        </div>
        {allow && !allow.locked && (
          <StringListField
            id={allow.key}
            label={t("privacy.allowlistExtra", "Extra allowed hosts")}
            help={help(allow)}
            value={allow.value ?? ""}
            placeholder={t("privacy.hostPlaceholder", "Add a host, then Enter")}
            onChange={(joined) => saveToast({ [allow.key]: joined })}
          />
        )}
      </Group>

      <Group
        title={t("privacy.groupLog", "Outbound log")}
        intro={t("privacy.groupLogIntro", "Everything that left this device, kept for review.")}
      >
        {onOpenLog && (
          <button
            type="button"
            onClick={onOpenLog}
            className="mb-4 inline-flex h-8 items-center gap-1.5 rounded-md border border-border px-2.5 text-sm transition-colors hover:bg-muted"
          >
            <ScrollText className="size-3.5" aria-hidden="true" />
            {tOut("openLog", "Open the outbound log")}
          </button>
        )}
        {retention && (
          <TextRow
            key={`${retention.key}:${retention.value ?? ""}`}
            field={retention}
            label={label(retention)}
            help={help(retention)}
            save={saveToast}
            type="number"
            suffix={t("privacy.days", "days")}
            narrow
          />
        )}
      </Group>

      {advanced.length > 0 && (
        <AdvancedSection title={t("privacy.advanced", "Review model and timeout")}>
          {advanced.map(renderField)}
        </AdvancedSection>
      )}
    </>
  );
}
