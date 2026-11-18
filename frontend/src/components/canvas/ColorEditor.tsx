/** A colour + its label, for a new palette colour (the popover's ＋) or an
 * existing one (My marks: recolour, rename). The label is required: it is
 * what the colour MEANS ("Method", "To verify"), shown on the swatch, in My
 * marks and on the note card. Yellow, grey and unreadable colours are
 * refused with the reason (yellow is reserved for citations). */
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { colorProblem, labelProblem, NAME_MAX } from "@/lib/marks/palette";
import { cn } from "@/lib/utils";
import type { MarkColor, MarkColorInfo } from "@/types/notes";

export interface ColorEditorProps {
  colors: readonly MarkColorInfo[];
  /** Editing this colour (its own label is not "taken"). */
  except?: MarkColor;
  initialHex?: string;
  initialName?: string;
  saveLabel: string;
  busy?: boolean;
  /** The server's refusal, shown under the fields. */
  error?: string | null;
  onSave: (hex: string, name: string) => void;
  onCancel: () => void;
}

export function ColorEditor({
  colors,
  except,
  initialHex = "#1f8a8a",
  initialName = "",
  saveLabel,
  busy = false,
  error = null,
  onSave,
  onCancel,
}: ColorEditorProps) {
  const { t } = useTranslation("notes");
  const id = useId();
  const [hex, setHex] = useState(initialHex);
  const [name, setName] = useState(initialName);
  const colour = colorProblem(hex);
  const label = labelProblem(name, colors, except);
  const touched = name !== initialName;
  const colourMsg =
    colour === "yellow"
      ? t("colors.problem.yellow", "Too close to yellow: yellow is reserved for citations.")
      : colour === "grey"
        ? t("colors.problem.grey", "Too grey: it would look like a mark with no colour.")
        : colour === "dark"
          ? t("colors.problem.dark", "Too dark to read the text under it.")
          : colour === "pale"
            ? t("colors.problem.pale", "Too pale to see on the page.")
            : colour === "invalid"
              ? t("colors.problem.invalid", "Pick a colour.")
              : null;
  const labelMsg =
    label === "long"
      ? t("colors.problem.long", { defaultValue: "At most {{n}} characters.", n: NAME_MAX })
      : label === "taken"
        ? t("colors.problem.taken", "Another colour already has this label.")
        : label === "empty" && touched
          ? t("colors.problem.empty", "A colour needs a label.")
          : null;
  const ok = !colour && !label && !busy;

  return (
    <form
      className="flex w-[244px] flex-col gap-1.5 p-1 text-xs"
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) onSave(hex.toLowerCase(), name.trim().replace(/\s+/g, " "));
      }}
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          e.stopPropagation();
          e.preventDefault();
          onCancel();
        }
      }}
    >
      <div className="flex items-center gap-2">
        <input
          type="color"
          aria-label={t("colors.pick", "Colour")}
          value={hex}
          onChange={(e) => setHex(e.target.value)}
          className="size-7 shrink-0 cursor-pointer rounded border border-border bg-transparent p-0"
        />
        <label htmlFor={`${id}-name`} className="sr-only">
          {t("colors.label", "What does this colour mean?")}
        </label>
        <input
          id={`${id}-name`}
          autoFocus
          maxLength={NAME_MAX + 10}
          value={name}
          placeholder={t("colors.labelPlaceholder", "What does this colour mean? e.g. To verify")}
          onChange={(e) => setName(e.target.value)}
          aria-invalid={label !== null && touched}
          className={cn(
            "h-7 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-xs outline-none focus-visible:ring-2 focus-visible:ring-ring",
          )}
        />
      </div>
      {(colourMsg || labelMsg || error) && (
        <p role="alert" className="text-[11.5px] leading-snug text-destructive">
          {colourMsg ?? labelMsg ?? error}
        </p>
      )}
      <div className="flex justify-end gap-1">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-2 py-1 transition-colors duration-150 hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
        >
          {t("colors.cancel", "Cancel")}
        </button>
        <button
          type="submit"
          disabled={!ok}
          className="rounded-md bg-primary px-2 py-1 text-primary-foreground transition-opacity duration-150 disabled:opacity-40"
        >
          {saveLabel}
        </button>
      </div>
    </form>
  );
}
