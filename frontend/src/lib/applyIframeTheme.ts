export const BASE_STYLE_ID = "ph-base-style";
export const DARK_STYLE_ID = "ph-dark-mode";

/**
 * Make a rendered paper document readable in the app's dark theme.
 *
 * The Citation Canvas iframe is style-isolated, and papers render with a light
 * background — a jarring white block inside a dark app. We inject a small
 * inversion stylesheet (invert + hue-rotate the page, then re-invert images so
 * figures keep their true colours) when `dark` is true, and remove it when
 * light. Idempotent: only one `<style id="ph-dark-mode">` ever exists.
 *
 * HTML mode only — a native PDF viewer in the iframe can't be styled this way.
 */
export function applyIframeTheme(doc: Document, dark: boolean): void {
  // Base style, applied in BOTH themes: fill every figure with a white
  // backdrop. Many figures are transparent PNGs (black strokes/text on an
  // empty background); without a fill their transparent areas show the page
  // through — invisible on the dark page, tinted on a light one. The white
  // backdrop only shows where the figure is transparent (opaque pixels cover
  // it), so the figure's own colours are unchanged. In dark mode the image's
  // re-invert filter (below) carries this white fill through the SAME double
  // inversion as the figure's own white pixels, so it blends seamlessly
  // instead of flipping to black.
  if (!doc.getElementById(BASE_STYLE_ID)) {
    const base = doc.createElement("style");
    base.id = BASE_STYLE_ID;
    base.textContent = "img { background-color: #ffffff; }";
    (doc.head ?? doc.documentElement).appendChild(base);
  }

  const existing = doc.getElementById(DARK_STYLE_ID);
  if (!dark) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const style = doc.createElement("style");
  style.id = DARK_STYLE_ID;
  // The invert trick: a normal white-bg/black-text page inverts to a dark bg
  // with light text. Set the base background to WHITE first so transparent
  // pages also invert to dark. Do NOT set a dark background here — the filter
  // would invert it back to light (white-on-white). Re-invert media so figures
  // keep their true colours.
  style.textContent = `
    html { background: #ffffff; filter: invert(0.9) hue-rotate(180deg); }
    img, svg, video, canvas, [style*="background-image"] {
      filter: invert(1) hue-rotate(180deg);
    }
  `;
  (doc.head ?? doc.documentElement).appendChild(style);
}
