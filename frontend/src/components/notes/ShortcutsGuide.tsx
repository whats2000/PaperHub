/** The shortcuts guide: everything the notes editor understands, in the
 * sections of the approved prototype. A popover anchored to `trigger` (or to
 * `anchor`, a control that opens it some other way, e.g. from a menu) that
 * grows from it and shrinks back into it. */
import { Fragment, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

import { GUIDE_SECTIONS, SUFFIX_EN, type Sample } from "./guideData";

function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="whitespace-nowrap rounded border border-border bg-background px-1 font-mono text-[11px] text-foreground">
      {children}
    </kbd>
  );
}

function Rendered({ sample, children }: { sample: Sample; children: ReactNode }) {
  switch (sample) {
    case "bold":
      return <b>{children}</b>;
    case "italic":
      return <i>{children}</i>;
    case "underline":
      return <u>{children}</u>;
    case "strike":
      return <s>{children}</s>;
    case "code":
      return <code className="rounded bg-accent px-1 font-mono text-[0.88em] text-[var(--note-code-fg)]">{children}</code>;
    case "hidden":
      return <span className="note-hidden">{children}</span>;
  }
}

export interface ShortcutsGuideProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The element that opens the guide, which it grows from and shrinks back into. */
  trigger?: React.ReactElement;
  /** Or: the element it grows from when opened some other way; focus goes
   * back to it on close. */
  anchor?: React.RefObject<HTMLElement | null>;
}

export function ShortcutsGuide({ open, onOpenChange, trigger, anchor }: ShortcutsGuideProps) {
  const { t } = useTranslation();
  return (
    <Popover open={open} onOpenChange={onOpenChange}>
      {trigger && <PopoverTrigger render={trigger} />}
      <PopoverContent
        side="bottom"
        align="end"
        sideOffset={6}
        role="dialog"
        anchor={anchor}
        finalFocus={anchor}
        aria-label={t("notes:guide.title", "Shortcuts")}
        className="max-h-[72vh] w-[340px] max-w-[calc(100vw-16px)] origin-top-right overflow-y-auto px-3 pt-1 pb-2.5 text-[12.5px] motion-reduce:animate-none motion-reduce:transition-none"
      >
        {GUIDE_SECTIONS.map((section) => (
          <section key={section.id}>
            <h4 className="mt-2.5 mb-0.5 text-[11.5px] font-semibold text-muted-foreground">
              {t(`notes:guide.sections.${section.id}`, section.title)}
            </h4>
            <table className="w-full border-collapse">
              <tbody>
                {section.rows.map((row) => (
                  <tr key={row.id}>
                    <td className="w-[48%] py-[3px] pr-2 align-baseline">
                      {row.plainLeft !== undefined ? (
                        t(`notes:guide.left.${row.id}`, row.plainLeft)
                      ) : (
                        <>
                          {row.keys?.map((k, i) => (
                            <Fragment key={k}>
                              {i > 0 && " "}
                              <Kbd>{k}</Kbd>
                            </Fragment>
                          ))}
                          {row.suffix && ` ${t(`notes:guide.suffix.${row.suffix}`, SUFFIX_EN[row.suffix])}`}
                        </>
                      )}
                    </td>
                    <td className="py-[3px] align-baseline">
                      {row.sample ? (
                        <>
                          <Rendered sample={row.sample}>{t(`notes:guide.rows.${row.id}`, row.right)}</Rendered>
                          {row.sample === "hidden" && ` · ${t("notes:guide.clickToShow", "click to show")}`}
                        </>
                      ) : (
                        <>
                          {t(`notes:guide.rows.${row.id}`, row.right)}
                          {row.rightKey && (
                            <>
                              {" · "}
                              <Kbd>{row.rightKey}</Kbd> {t(`notes:guide.rows.${row.id}Stops`, row.rightAfter ?? "")}
                            </>
                          )}
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        ))}
      </PopoverContent>
    </Popover>
  );
}
