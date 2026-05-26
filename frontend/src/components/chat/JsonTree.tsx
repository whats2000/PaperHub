import JsonView from "@uiw/react-json-view";

// ---------------------------------------------------------------------------
// JsonTree — readable, collapsible tree rendering of arbitrary trace payloads.
//
// The trace's `args_redacted_json` / `result_summary_json` frequently nest
// JSON *as strings* (the tracer records e.g. chunk-ID arrays or tool-result
// payloads that were themselves serialized). `JSON.stringify(v, null, 2)` then
// double-escapes them — `\"`, `\n` everywhere — which is unreadable.
//
// We render with `@uiw/react-json-view`: a real interactive tree (branch
// guides, collapse/expand, copy) themed via the app's own CSS tokens so it
// matches light/dark automatically. Before handing the value over we
// `deepParse` it — recursively replacing any string that is itself valid JSON
// with the parsed structure — so embedded payloads unfold into the tree
// instead of showing as escaped blobs.
// ---------------------------------------------------------------------------

function isContainer(v: unknown): v is object {
  return v !== null && typeof v === "object";
}

// Interpret a string as embedded JSON when it looks like a container; else
// leave it as-is (don't coerce bare numbers / mangle prose).
function maybeParse(value: string): unknown {
  const trimmed = value.trim();
  const first = trimmed[0];
  if (first !== "{" && first !== "[") return value;
  try {
    const parsed = JSON.parse(trimmed) as unknown;
    if (isContainer(parsed)) return parsed;
  } catch {
    /* not JSON */
  }
  return value;
}

// Recursively deep-parse embedded JSON strings throughout the structure.
function deepParse(value: unknown): unknown {
  const v = typeof value === "string" ? maybeParse(value) : value;
  if (Array.isArray(v)) return (v as unknown[]).map(deepParse);
  if (isContainer(v)) {
    const out: Record<string, unknown> = {};
    for (const [k, val] of Object.entries(v as Record<string, unknown>)) {
      out[k] = deepParse(val);
    }
    return out;
  }
  return v;
}

// Theme bound to the app's CSS tokens — adapts to light/dark with no JS switch.
// Mid-tone accents for scalars stay legible on both backgrounds.
const theme = {
  "--w-rjv-font-family": "inherit",
  "--w-rjv-background-color": "transparent",
  "--w-rjv-color": "var(--muted-foreground)",
  "--w-rjv-line-color": "var(--border)",
  "--w-rjv-arrow-color": "var(--muted-foreground)",
  "--w-rjv-info-color": "var(--muted-foreground)",
  "--w-rjv-curlybraces-color": "var(--muted-foreground)",
  "--w-rjv-brackets-color": "var(--muted-foreground)",
  "--w-rjv-colon-color": "var(--muted-foreground)",
  "--w-rjv-ellipsis-color": "var(--muted-foreground)",
  "--w-rjv-key-string": "var(--foreground)",
  "--w-rjv-quotes-color": "var(--muted-foreground)",
  "--w-rjv-quotes-string-color": "var(--foreground)",
  "--w-rjv-type-string-color": "var(--foreground)",
  "--w-rjv-type-int-color": "#8b5cf6",
  "--w-rjv-type-float-color": "#8b5cf6",
  "--w-rjv-type-bigint-color": "#8b5cf6",
  "--w-rjv-type-boolean-color": "#0ea5e9",
  "--w-rjv-type-null-color": "#0ea5e9",
  "--w-rjv-type-undefined-color": "#0ea5e9",
  "--w-rjv-copied-color": "var(--muted-foreground)",
  "--w-rjv-copied-success-color": "#16a34a",
} as const;

// A bare scalar can't root a JSON tree — render it as plain (raw) text.
function PlainLeaf({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-muted-foreground/60 italic">null</span>;
  }
  const text =
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean" || typeof value === "bigint"
        ? String(value)
        : typeof value; // symbol / function — describe by type
  return (
    <span className="whitespace-pre-wrap break-words text-foreground/90">{text}</span>
  );
}

export function JsonTree({ value }: { value: unknown }) {
  const v = deepParse(value);

  if (!isContainer(v)) {
    return <PlainLeaf value={v} />;
  }

  return (
    <JsonView
      value={v}
      collapsed={2}
      displayDataTypes={false}
      displayObjectSize={false}
      shortenTextAfterLength={0}
      style={{ ...theme, fontSize: "11px", lineHeight: 1.5 }}
    />
  );
}
