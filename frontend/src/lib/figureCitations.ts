import { cn } from "@/lib/utils";

/** Matches `[fig:<paper>/<figure id>]` markers — a figure image the agent
 * looked at — including lists like `[fig:20/fig-003, 20/fig-004]` or
 * `[fig:20/fig-003, fig:20/fig-004]`. Capture group 1 is the raw key list. */
export const FIG_MARKER_RE =
  /\[fig:\s*(\d+\s*\/\s*[A-Za-z0-9_.-]+(?:\s*,\s*(?:fig:\s*)?\d+\s*\/\s*[A-Za-z0-9_.-]+)*)\s*\]/g;

/** Matches `[view:<paper>/<page>/<x0>,<y0>,<x1>,<y1>]` — a page region the
 * agent viewed (1-based page, box in 0-1000 page coordinates). Capture
 * group 1 is the key, as the backend's `view_key` spells it after trimming. */
export const VIEW_MARKER_RE =
  /\[view:\s*(\d+\s*\/\s*\d+\s*\/\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*\d+)\s*\]/g;

/** Either marker, in text order: group 1 = figure key list, group 2 = view key. */
export const EVIDENCE_MARKER_RE = new RegExp(
  `${FIG_MARKER_RE.source}|${VIEW_MARKER_RE.source}`,
  "g",
);

export interface FigureRef {
  paper: number;
  figureId: string;
  /** `"<paper>/<figure id>"`, the identity used for numbering. */
  key: string;
}

export interface ViewRef {
  paper: number;
  /** 1-based page on the paper's page PDF. */
  page: number;
  box: [number, number, number, number];
  /** `"view:<paper>/<page>/<x0>,<y0>,<x1>,<y1>"`, the numbering identity. */
  key: string;
}

/** Parse a marker's key-list capture into figure references. */
export function parseFigureKeys(list: string): FigureRef[] {
  return [...list.matchAll(/(\d+)\s*\/\s*([A-Za-z0-9_.-]+)/g)].map((m) => ({
    paper: Number(m[1]),
    figureId: m[2] ?? "",
    key: `${m[1]}/${m[2]}`,
  }));
}

/** Parse a `[view:]` capture (spaces allowed) into a region reference. */
export function parseViewKey(raw: string): ViewRef {
  const n = (raw.match(/\d+/g) ?? []).map(Number);
  const box: [number, number, number, number] = [
    n[2] ?? 0,
    n[3] ?? 0,
    n[4] ?? 0,
    n[5] ?? 0,
  ];
  return {
    paper: n[0] ?? 0,
    page: n[1] ?? 0,
    box,
    key: `view:${n[0]}/${n[1]}/${box.join(",")}`,
  };
}

/** Map each distinct cited figure or page region to its ordinal (1-based,
 * first-seen order). Figures and regions share one sequence: both are
 * images the agent looked at. */
export function buildFigureOrdinalMap(content: string): Map<string, number> {
  const map = new Map<string, number>();
  const add = (key: string) => {
    if (!map.has(key)) map.set(key, map.size + 1);
  };
  for (const match of content.matchAll(EVIDENCE_MARKER_RE)) {
    if (match[1] !== undefined) {
      for (const ref of parseFigureKeys(match[1])) add(ref.key);
    } else if (match[2] !== undefined) {
      add(parseViewKey(match[2]).key);
    }
  }
  return map;
}

/** Shared look of the evidence superscripts (figure and page region). */
export const EVIDENCE_MARKER_CLASS = cn(
  "mx-0.5 inline-flex cursor-pointer items-center gap-0.5 rounded px-1 text-[0.7em] font-medium text-primary",
  "transition-colors duration-150 motion-reduce:transition-none",
  "hover:bg-primary/10 hover:underline",
);
