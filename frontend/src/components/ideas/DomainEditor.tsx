import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Merge, Plus, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { usePresence } from "@/hooks/usePresence";
import { domainColorVar } from "@/lib/domainColors";
import { prefersReducedMotion } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { useIdeationStore } from "@/store/ideation";
import type { GraphDocument, GraphDomain } from "@/types/ideation";

/** Matches the tag's zoom-out before the edit is sent. */
const TAG_EXIT_MS = 150;

function Label({ children, htmlFor }: { children: React.ReactNode; htmlFor?: string }) {
  return (
    <label htmlFor={htmlFor} className="mt-3 mb-1.5 block text-[11.5px] text-muted-foreground">
      {children}
    </label>
  );
}

type Option = { kind: "add"; domain: GraphDomain } | { kind: "new"; name: string };

/**
 * A document's domain tags in the drawer: remove (×), add with autocomplete
 * over the existing domains (or create one), and click a tag to rename or
 * merge it. Edits go to the manual endpoints, which re-analysis never overwrites.
 */
export function DocDomainEditor({
  doc,
  domains,
  onOpenDomain,
}: {
  doc: GraphDocument;
  domains: GraphDomain[];
  onOpenDomain: (domainId: number) => void;
}) {
  const { t } = useTranslation("ideas");
  const setDocumentDomains = useIdeationStore((s) => s.setDocumentDomains);
  const [query, setQuery] = useState("");
  const [focused, setFocused] = useState(false);
  const [active, setActive] = useState(0);
  // Tags shrinking away; their removal is sent together when the exit has played.
  const [removing, setRemoving] = useState<number[]>([]);
  const pendingRemovals = useRef(new Set<number>());
  const removeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(
    () => () => {
      if (removeTimer.current !== null) clearTimeout(removeTimer.current);
    },
    [],
  );
  // Adding waits for pending removals and any request in flight.
  const locked = busy || removing.length > 0;
  const listId = useId();
  const inputId = useId();

  const byId = new Map(domains.map((d) => [d.id, d]));
  const q = query.trim();
  const ql = q.toLowerCase();
  const options: Option[] = domains
    .filter((d) => !doc.domains.includes(d.id) && d.name.toLowerCase().includes(ql))
    .map((domain) => ({ kind: "add" as const, domain }));
  if (q && !domains.some((d) => d.name.toLowerCase() === ql)) options.push({ kind: "new", name: q });
  const listOpen = focused && options.length > 0;
  const list = usePresence(listOpen);
  const activeIndex = Math.min(active, Math.max(0, options.length - 1));

  // The document's domains as the store has them now, not as this render saw them.
  const currentDomains = () =>
    useIdeationStore.getState().graph?.documents.find((d) => d.id === doc.id)?.domains ?? doc.domains;

  const save = async (domainIds: number[], newNames: string[]) => {
    setBusy(true);
    try {
      await setDocumentDomains(doc.id, { domain_ids: domainIds, new_names: newNames });
    } catch {
      toast.error(t("domainEditor.failed", "Couldn't update the domains"));
    } finally {
      setBusy(false);
    }
  };

  const remove = (id: number) => {
    if (busy || pendingRemovals.current.has(id)) return;
    const left = currentDomains().filter((d) => d !== id && !pendingRemovals.current.has(d));
    if (left.length === 0) {
      toast(t("domainEditor.lastDomain", "A document needs at least one domain"));
      return;
    }
    // The tag shrinks away first; removals clicked during the exit join one edit.
    pendingRemovals.current.add(id);
    setRemoving([...pendingRemovals.current]);
    if (removeTimer.current !== null) return;
    removeTimer.current = setTimeout(
      () => {
        removeTimer.current = null;
        const gone = new Set(pendingRemovals.current);
        pendingRemovals.current.clear();
        void save(
          currentDomains().filter((d) => !gone.has(d)),
          [],
        ).finally(() => setRemoving([]));
      },
      prefersReducedMotion() ? 0 : TAG_EXIT_MS,
    );
  };

  const choose = (o: Option) => {
    if (locked) return;
    setQuery("");
    setActive(0);
    // Close the list; typing again reopens it.
    setFocused(false);
    const now = currentDomains();
    if (o.kind === "add") void save(now.includes(o.domain.id) ? now : [...now, o.domain.id], []);
    else void save(now, [o.name]);
  };

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      if (!options.length) return;
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((activeIndex + step + options.length) % options.length);
    } else if (e.key === "Enter") {
      const o = options[activeIndex];
      if (listOpen && o && !locked) {
        e.preventDefault();
        choose(o);
      }
    } else if (e.key === "Escape") {
      // First Escape clears the query, the next closes the list.
      if (q) setQuery("");
      else setFocused(false);
    }
  };

  const optionLabel = (o: Option) =>
    o.kind === "add" ? o.domain.name : t("domainEditor.create", "Create domain “{{name}}”", { name: o.name });

  return (
    <div data-testid="domain-editor">
      <Label>{t("domainEditor.label", "Domain tags (manual edits survive re-analysis)")}</Label>
      <div className="flex flex-wrap gap-1.5">
        {doc.domains.map((id) => {
          const d = byId.get(id);
          if (!d) return null;
          return (
            <span
              key={id}
              className={cn(
                "inline-flex h-6 items-center rounded-md bg-secondary text-xs animate-in fade-in-0 zoom-in-95 duration-150 motion-reduce:animate-none",
                removing.includes(id) && "pointer-events-none animate-out fade-out-0 zoom-out-95 fill-mode-forwards",
              )}
            >
              <button
                type="button"
                onClick={() => onOpenDomain(id)}
                title={t("domainEditor.openDomain", "Rename or merge {{name}}", { name: d.name })}
                className="inline-flex h-full items-center gap-1.5 rounded-l-md pl-2 pr-1 hover:bg-accent focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                <span aria-hidden="true" className="size-2 rounded-full" style={{ background: domainColorVar(d.color_index) }} />
                {d.name}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={() => remove(id)}
                aria-label={t("domainEditor.remove", "Remove {{name}}", { name: d.name })}
                className="inline-flex h-full items-center rounded-r-md px-1 text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50 focus-visible:outline-none"
              >
                <X className="size-3" aria-hidden="true" />
              </button>
            </span>
          );
        })}
      </div>
      <div className="relative mt-2">
        <Plus className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
        <Input
          id={inputId}
          role="combobox"
          aria-expanded={listOpen}
          aria-controls={listId}
          aria-autocomplete="list"
          aria-activedescendant={listOpen ? `${listId}-${activeIndex}` : undefined}
          aria-label={t("domainEditor.addLabel", "Add a domain")}
          placeholder={t("domainEditor.addPlaceholder", "Add a domain…")}
          value={query}
          // Not `disabled`: that drops focus mid-edit. Typing is ignored while locked.
          readOnly={locked}
          aria-disabled={locked || undefined}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
            setFocused(true);
          }}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
          onKeyDown={onKey}
          className="h-8 pl-8"
        />
        {list.mounted && (
          <ul
            id={listId}
            role="listbox"
            data-state={list.state}
            aria-label={t("domainEditor.suggestions", "Domain suggestions")}
            className={cn(
              "absolute top-full right-0 left-0 z-30 mt-1 max-h-48 origin-top overflow-y-auto rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
              "duration-150 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 motion-reduce:animate-none",
            )}
          >
            {options.map((o, i) => (
              <li
                key={o.kind === "add" ? `d${o.domain.id}` : "new"}
                id={`${listId}-${i}`}
                role="option"
                aria-selected={i === activeIndex}
                // Keep focus in the input so the list doesn't blur away first.
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => choose(o)}
                className={cn(
                  "flex cursor-default items-center gap-2 rounded px-2 py-1.5 text-sm",
                  i === activeIndex && "bg-muted",
                )}
              >
                {o.kind === "add" ? (
                  <span aria-hidden="true" className="size-2 rounded-full" style={{ background: domainColorVar(o.domain.color_index) }} />
                ) : (
                  <Plus className="size-3.5" aria-hidden="true" />
                )}
                {optionLabel(o)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** A domain selected in the graph: rename it, or merge it into another domain. */
export function DomainSettings({
  domain,
  domains,
  onMerged,
}: {
  domain: GraphDomain;
  domains: GraphDomain[];
  onMerged: (targetId: number) => void;
}) {
  const { t } = useTranslation("ideas");
  const patchDomain = useIdeationStore((s) => s.patchDomain);
  const [name, setName] = useState(domain.name);
  const others = domains.filter((d) => d.id !== domain.id);
  const [picked, setTarget] = useState<string>(others[0] ? String(others[0].id) : "");
  // The list can change under us (a refetch, another merge): fall back to the first domain left.
  const target = others.some((d) => String(d.id) === picked) ? picked : others[0] ? String(others[0].id) : "";
  const [busy, setBusy] = useState(false);
  const nameId = useId();

  const run = async (body: { name?: string; merge_into?: number }, done: string) => {
    setBusy(true);
    try {
      await patchDomain(domain.id, body);
      toast.success(done);
      return true;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      if (body.name !== undefined && /\b409\b/.test(msg)) {
        toast.error(
          t("domain.nameTaken", "A domain named “{{name}}” already exists — merge into it instead?", { name: body.name }),
        );
      } else {
        toast.error(t("domain.failed", "Couldn't update the domain"));
      }
      return false;
    } finally {
      setBusy(false);
    }
  };
  const trimmed = name.trim();
  const rename = () => void run({ name: trimmed }, t("domain.renamed", "Domain renamed"));
  const merge = async () => {
    const into = Number(target);
    const targetName = others.find((d) => d.id === into)?.name ?? "";
    if (await run({ merge_into: into }, t("domain.merged", "Merged into {{name}}", { name: targetName }))) {
      onMerged(into);
    }
  };

  return (
    <div data-testid="domain-settings">
      <Label htmlFor={nameId}>{t("domain.rename", "Rename")}</Label>
      <form
        className="flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed && trimmed !== domain.name) rename();
        }}
      >
        <Input id={nameId} value={name} onChange={(e) => setName(e.target.value)} className="h-8" />
        <Button type="submit" variant="outline" size="sm" disabled={busy || !trimmed || trimmed === domain.name}>
          {t("domain.save", "Save")}
        </Button>
      </form>
      {others.length > 0 && (
        <>
          <Label>{t("domain.mergeInto", "Merge into")}</Label>
          <div className="flex gap-2">
            <Select
              value={target}
              onValueChange={setTarget}
              options={others.map((d) => ({ value: String(d.id), label: d.name }))}
              aria-label={t("domain.mergeInto", "Merge into")}
              className="min-w-0 flex-1"
            />
            <Button variant="outline" size="sm" disabled={busy || !target} onClick={() => void merge()}>
              <Merge aria-hidden="true" />
              {t("domain.merge", "Merge")}
            </Button>
          </div>
          <p className="mt-1.5 text-xs text-muted-foreground">
            {t("domain.mergeHint", "Its documents move to the chosen domain, and this domain goes away.")}
          </p>
        </>
      )}
    </div>
  );
}
