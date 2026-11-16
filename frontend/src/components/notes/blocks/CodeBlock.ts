/** BlockNote's code block plus a Copy button.
 *
 * The block's own render (language select, `<pre><code>`) is kept; the button
 * is added beside it. It copies the code as it is now and says "Copied" for a
 * moment (the colour change is a CSS transition, see `.note-code-copy`).
 *
 * BlockNote's own fence rule is left out: it also fired on Enter in the middle
 * of a "```lang" line and mangled the rest of it. `codeFence` (inlineMarks.ts)
 * makes code blocks instead, only at the end of the line.
 */
import { codeBlockOptions } from "@blocknote/code-block";
import { createCodeBlockSpec, createExtension } from "@blocknote/core";

import i18n from "@/lib/i18n";

const COPIED_MS = 1500;

type SpecExtension = NonNullable<ReturnType<typeof createCodeBlockSpec>["extensions"]>[number];

/** The extension without its input rules. An extension made from a plain
 * definition returns that definition when called; others are kept as they are. */
function withoutInputRules(ext: SpecExtension): SpecExtension {
  if (typeof ext !== "function") return ext;
  const def = (ext as unknown as () => unknown)();
  if (!def || typeof def !== "object" || !("inputRules" in def)) return ext;
  return createExtension({ ...(def as Record<string, unknown>), inputRules: undefined } as never) as SpecExtension;
}

export function codeBlockWithCopy() {
  const base = createCodeBlockSpec(codeBlockOptions);
  const baseRender = base.implementation.render!;
  return {
    ...base,
    extensions: base.extensions?.map(withoutInputRules),
    implementation: {
      ...base.implementation,
      render(this: ThisParameterType<typeof baseRender>, ...args: Parameters<NonNullable<typeof baseRender>>) {
        // BlockNote throws on a language it does not know (a fence typed
        // mid-line, stored data from another version): show plain text instead.
        const [block, ...rest] = args;
        const known = codeBlockOptions.supportedLanguages ?? {};
        const safe =
          block.props.language in known ? block : { ...block, props: { ...block.props, language: "text" } };
        const rendered = baseRender.apply(this, [safe, ...rest] as typeof args);
        const button = document.createElement("button");
        button.type = "button";
        button.className = "note-code-copy";
        button.contentEditable = "false";
        const label = (copied: boolean) =>
          copied ? i18n.t("notes:code.copied", "Copied") : i18n.t("notes:code.copy", "Copy");
        const paint = (copied: boolean) => {
          button.textContent = label(copied);
          button.setAttribute("aria-label", label(copied));
          if (copied) button.setAttribute("data-copied", "");
          else button.removeAttribute("data-copied");
        };
        paint(false);
        let timer: ReturnType<typeof setTimeout> | undefined;
        button.addEventListener("mousedown", (e) => e.preventDefault()); // keep the caret
        button.addEventListener("click", () => {
          const code = rendered.contentDOM?.textContent ?? "";
          void navigator.clipboard?.writeText(code).then(
            () => {
              paint(true);
              clearTimeout(timer);
              timer = setTimeout(() => paint(false), COPIED_MS);
            },
            () => undefined,
          );
        });
        rendered.dom.appendChild(button);
        const destroy = rendered.destroy;
        return {
          ...rendered,
          destroy: () => {
            clearTimeout(timer);
            destroy?.();
          },
        };
      },
    },
  };
}
