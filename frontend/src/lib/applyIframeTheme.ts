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
  const existing = doc.getElementById(DARK_STYLE_ID);
  if (!dark) {
    existing?.remove();
    return;
  }
  if (existing) return;
  const style = doc.createElement("style");
  style.id = DARK_STYLE_ID;
  style.textContent = `
    html { background: #0f1115 !important; filter: invert(0.92) hue-rotate(180deg); }
    img, svg, video, canvas, [style*="background-image"] {
      filter: invert(1) hue-rotate(180deg);
    }
  `;
  (doc.head ?? doc.documentElement).appendChild(style);
}
