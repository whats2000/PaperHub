/** Mark kinds, styles and the user's colour palette (Plan H5b.1, 2026-11-19).
 *
 * A mark's `kind` says WHAT was marked, a text mark's `style` HOW it is
 * drawn; `color` is a palette key or null (no colour: a neutral grey). Each
 * palette row carries the user's light-mode `hex` and a derived `dark`.
 * `colorProblem` mirrors the server's check (`notes/mark_colors.py`): yellow
 * is reserved for citations, grey reads as "no colour", and the colour must
 * show on the page. */
import type { Mark, MarkColor, MarkColorInfo, MarkKind, MarkStyle } from "@/types/notes";

export const MARK_STYLES: readonly MarkStyle[] = ["highlight", "underline", "wavy", "bar"];

/** English names (the `notes:marks.style.*` fallbacks). */
export const STYLE_NAME: Record<MarkStyle, string> = {
  highlight: "Highlight",
  underline: "Underline",
  wavy: "Wavy",
  bar: "Bar",
};


/** The four defaults (validated with the dataviz palette checker, both modes). */
export const DEFAULT_PALETTE: Record<string, { light: string; dark: string }> = {
  green: { light: "#2f9e63", dark: "#36a067" },
  blue: { light: "#3b7dd8", dark: "#4b86dc" },
  pink: { light: "#d6457e", dark: "#d9578a" },
  purple: { light: "#8b5cd6", dark: "#9670dc" },
};

/** Greys for a mark with no colour (underline / wavy / bar). */
export const NO_COLOR = { light: "#7b7b7b", dark: "#8a8a8a" };

const LEGACY: Record<string, [MarkKind, MarkStyle]> = {
  highlight: ["text", "highlight"],
  quote: ["text", "underline"],
};

/** What a mark (or a mark block's props) is, read the new way: a mark saved
 * before kind + style ("highlight" / "quote") is text in its old style. */
export function kindAndStyle(m: { kind?: string | null; style?: string | null }): {
  kind: MarkKind;
  style: MarkStyle | null;
} {
  const legacy = LEGACY[m.kind ?? ""];
  if (legacy) return { kind: legacy[0], style: legacy[1] };
  const kind = (m.kind || "text") as MarkKind;
  if (kind !== "text") return { kind, style: null };
  const style = MARK_STYLES.includes(m.style as MarkStyle) ? (m.style as MarkStyle) : "highlight";
  return { kind, style };
}

/** A text mark (one drawn over words, in a style). */
export function isTextMark(m: Pick<Mark, "kind"> & { style?: string | null }): boolean {
  return kindAndStyle(m).kind === "text";
}

/** The colour a key draws in, for the theme (null: no colour). */
export function colorHex(colors: readonly MarkColorInfo[], key: MarkColor | null, dark: boolean): string | null {
  if (!key) return null;
  const info = colors.find((c) => c.color === key);
  const own = dark ? info?.dark : info?.hex;
  if (own) return own;
  const d = DEFAULT_PALETTE[key];
  return d ? (dark ? d.dark : d.light) : null;
}

// ---- OKLCH, as the server computes it -----------------------------------

const lin = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);

export function oklch(hex: string): [number, number, number] | null {
  const m = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = Number.parseInt(m[1]!, 16);
  const [r, g, b] = [16, 8, 0].map((s) => lin(((n >> s) & 255) / 255)) as [number, number, number];
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const mm = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  const L = 0.2104542553 * l + 0.793617785 * mm - 0.0040720468 * s;
  const A = 1.9779984951 * l - 2.428592205 * mm + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * mm - 0.808675766 * s;
  const h = ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360;
  return [L, Math.hypot(A, B), h];
}

export type ColorProblem = "invalid" | "grey" | "yellow" | "dark" | "pale";

/** Why a colour cannot be a mark colour (null: it can). */
export function colorProblem(hex: string): ColorProblem | null {
  const c = oklch(hex);
  if (!c) return "invalid";
  const [L, C, h] = c;
  if (C < 0.07) return "grey";
  if (h >= 70 && h <= 120) return "yellow";
  if (L < 0.4) return "dark";
  if (L > 0.8) return "pale";
  return null;
}

export const NAME_MAX = 40;

/** Why a label cannot be saved (null: it can). Trimmed, required, unique
 * among the user's colours (ignoring case), at most NAME_MAX characters. */
export function labelProblem(
  name: string,
  colors: readonly MarkColorInfo[],
  except?: MarkColor,
): "empty" | "long" | "taken" | null {
  const n = name.trim().replace(/\s+/g, " ");
  if (!n) return "empty";
  if (n.length > NAME_MAX) return "long";
  const fold = n.toLocaleLowerCase();
  if (colors.some((c) => c.color !== except && c.name.toLocaleLowerCase() === fold)) return "taken";
  return null;
}

/** A text mark drawn in the app's own DOM (the translation card, the note
 * card's chips), from the `--hl-*` tokens; no colour is the muted grey. A
 * bar is placed by the caller (beside the lines). */
export function textMarkCss(style: MarkStyle | null, color: MarkColor | null): Record<string, string | number> {
  const c = color ? `var(--hl-${color})` : "var(--muted-foreground)";
  switch (style) {
    case "underline":
      return { borderBottom: `2px solid ${c}`, borderRadius: 0 };
    case "wavy":
      return {
        borderRadius: 0,
        background: [
          `linear-gradient(135deg, transparent 40%, ${c} 40% 60%, transparent 60%) 0 100% / 6px 4px repeat-x`,
          `linear-gradient(45deg, transparent 40%, ${c} 40% 60%, transparent 60%) 3px 100% / 6px 4px repeat-x`,
        ].join(", "),
      };
    case "bar":
      return { background: c, borderRadius: 1.5 };
    default:
      return { background: `color-mix(in srgb, ${c} 26%, transparent)` };
  }
}
