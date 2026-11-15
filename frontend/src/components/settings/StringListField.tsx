import { X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { cn } from "@/lib/utils";

interface Item {
  id: number;
  text: string;
  /** Playing its exit animation; already removed from the saved list. */
  leaving: boolean;
}

const LEAVE_MS = 150;

let seq = 0;
const mk = (text: string): Item => ({ id: seq++, text, leaving: false });

function parse(value: string): string[] {
  return value
    .split(/[,\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}

/**
 * Chip editor for a comma-joined list setting (`string_list`). Adds on Enter or
 * comma, removes with the chip's x, and calls `onChange` once per edit with the
 * joined list ("a, b"). Chips update optimistically and fade/zoom in and out;
 * if `onChange` rejects they roll back (animated) to the last saved value.
 */
export function StringListField({
  id,
  label,
  value,
  onChange,
  locked = false,
  placeholder,
  help,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (joined: string) => Promise<void> | void;
  locked?: boolean;
  placeholder?: string;
  help?: string;
}) {
  const { t } = useTranslation("settings");
  const [items, setItems] = useState<Item[]>(() => parse(value).map(mk));
  const [draft, setDraft] = useState("");
  const timers = useRef<number[]>([]);
  const savedRef = useRef(value);
  useEffect(() => {
    savedRef.current = value;
  }, [value]);

  // Follow the saved value (e.g. after a refetch) unless it already matches
  // what we show, so an in-flight leave animation is not cut short. Adjusted
  // during render (not in an effect) to avoid a cascading re-render.
  const [seen, setSeen] = useState(value);
  if (seen !== value) {
    setSeen(value);
    const liveNow = items.filter((i) => !i.leaving).map((i) => i.text);
    const next = parse(value);
    if (liveNow.join("|") !== next.join("|")) {
      setItems(next.map(mk));
    }
  }

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((h) => window.clearTimeout(h));
  }, []);

  const live = (list: Item[]) => list.filter((i) => !i.leaving).map((i) => i.text);

  function sweepSoon() {
    const h = window.setTimeout(() => {
      setItems((prev) => prev.filter((i) => !i.leaving));
    }, LEAVE_MS);
    timers.current.push(h);
  }

  /** The save failed: animate back to what is actually saved. */
  function rollback() {
    const saved = parse(savedRef.current);
    setItems((prev) => {
      const kept = prev.map((i) =>
        !i.leaving && !saved.includes(i.text) ? { ...i, leaving: true } : i,
      );
      const have = new Set(kept.filter((i) => !i.leaving).map((i) => i.text));
      return [...kept, ...saved.filter((x) => !have.has(x)).map(mk)];
    });
    sweepSoon();
  }

  function commit(joined: string) {
    void Promise.resolve()
      .then(() => onChange(joined))
      .catch(rollback);
  }

  function addMany(raws: string[]) {
    setDraft("");
    const cur = live(items);
    const fresh: string[] = [];
    for (const raw of raws) {
      const text = raw.trim();
      if (!text) continue;
      const lower = text.toLowerCase();
      if ([...cur, ...fresh].some((c) => c.toLowerCase() === lower)) continue;
      fresh.push(text);
    }
    if (fresh.length === 0) return;
    setItems((prev) => [...prev, ...fresh.map(mk)]);
    commit([...cur, ...fresh].join(", "));
  }

  function remove(itemId: number) {
    const target = items.find((i) => i.id === itemId);
    if (!target) return;
    const rest = live(items).filter((c) => c !== target.text);
    setItems((prev) => prev.map((i) => (i.id === itemId ? { ...i, leaving: true } : i)));
    commit(rest.join(", "));
    sweepSoon();
  }

  return (
    <div className="mb-4">
      <label htmlFor={id} className="text-sm font-medium">
        {label}
      </label>
      {help && <p className="mt-1 text-xs text-muted-foreground">{help}</p>}
      <div
        className={cn(
          "mt-1.5 flex flex-wrap items-center gap-1.5 rounded-md border border-border bg-background p-1.5 focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50",
          locked && "opacity-60",
        )}
      >
        {items.map((i) => (
          <span
            key={i.id}
            data-state={i.leaving ? "closed" : "open"}
            className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs transition-[opacity,transform] duration-150 ease-out data-[state=closed]:scale-95 data-[state=closed]:opacity-0 data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:transition-none motion-reduce:animate-none"
          >
            {i.text}
            {!locked && !i.leaving && (
              <button
                type="button"
                aria-label={t("privacy.removeItem", "Remove {{item}}", { item: i.text })}
                className="rounded-full text-muted-foreground hover:text-foreground"
                onClick={() => remove(i.id)}
              >
                <X className="size-3" />
              </button>
            )}
          </span>
        ))}
        <input
          id={id}
          value={draft}
          disabled={locked}
          placeholder={placeholder ?? t("privacy.addItemPlaceholder", "Add one, then press Enter")}
          onChange={(e) => {
            const v = e.target.value;
            if (v.includes(",")) {
              const parts = v.split(",");
              addMany(parts.slice(0, -1));
              setDraft(parts[parts.length - 1] ?? "");
            } else setDraft(v);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addMany([draft]);
            }
          }}
          onBlur={() => addMany([draft])}
          className="h-6 min-w-32 flex-1 bg-transparent px-1 text-sm outline-none disabled:cursor-not-allowed"
        />
      </div>
    </div>
  );
}
