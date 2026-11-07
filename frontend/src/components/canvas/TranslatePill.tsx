import { useId, useState, type KeyboardEvent } from "react";
import type { TFunction } from "i18next";
import { Check, ChevronDown, Languages, RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";

import { usePresence } from "@/hooks/usePresence";
import { LANGUAGE_ENDONYMS, SUPPORTED_LANGUAGES } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useTranslationStore } from "@/store/translation";

export type TranslateDisabledReason =
  | "no_blocks"
  | "no_inline_math_llm"
  | "load_failed";

interface Props {
  paperId: number;
  /** Set when this paper can't be translated; the pill stays, disabled. */
  disabledReason?: TranslateDisabledReason | null;
  /** Whether this paper can be translated isn't known yet (its document or
   *  PDF paragraphs are still loading): the pill stays, non-interactive and
   *  aria-busy, with no tooltip. */
  pending?: boolean;
  /** The disabled pill was hovered or focused: a chance to re-check why. */
  onDisabledInteract?: () => void;
  /** Cached translations predate the current terminology memory. */
  staleTerminology: boolean;
  onRetranslate: () => void;
}

/** Matches the label's closed-state `duration-200` transition. */
const LABEL_EXIT_MS = 200;

function disabledText(
  reason: TranslateDisabledReason,
  t: TFunction<"translation">,
): string {
  switch (reason) {
    case "no_blocks":
      return t("disabled.no_blocks", "This PDF has no paragraph layout yet");
    case "no_inline_math_llm":
      return t(
        "disabled.no_inline_math_llm",
        "Formulas in this PDF couldn't be read; translation is off. Set a Marker LLM model and re-process this PDF.",
      );
    case "load_failed":
      return t(
        "disabled.load_failed",
        "This paper didn't load, so it can't be translated",
      );
    default: {
      const exhaustive: never = reason;
      return exhaustive;
    }
  }
}

const UI_LANGUAGES = SUPPORTED_LANGUAGES.map((l) => LANGUAGE_ENDONYMS[l]);

/**
 * Floating translate control for the Citation Canvas (HTML and PDF). The round
 * icon toggles translation for this paper; when on, a language label grows
 * leftward out of it (the icon never moves) and opens the language menu.
 */
export function TranslatePill({
  paperId,
  disabledReason = null,
  pending = false,
  onDisabledInteract,
  staleTerminology,
  onRetranslate,
}: Props) {
  const { t, i18n } = useTranslation("translation");
  const language = useTranslationStore((s) => s.language);
  const recent = useTranslationStore((s) => s.recent);
  const enabled = useTranslationStore((s) => !!s.enabledByPaper[paperId]);
  const setLanguage = useTranslationStore((s) => s.setLanguage);
  const toggle = useTranslationStore((s) => s.toggle);

  const hasReason = disabledReason !== null;
  const disabled = hasReason || pending;
  const reasonText = hasReason ? disabledText(disabledReason, t) : "";
  const reasonId = useId();
  const pillName = t("pill.toggle", "Translate paper");
  // A pending pill keeps a remembered "on" label (no collapse-then-regrow).
  const on = enabled && !hasReason;
  // English UI and no language ever picked: turning on would translate
  // English into English, so the first toggle-on asks for a language first.
  const mustChoose = recent.length === 0 && language === LANGUAGE_ENDONYMS.en;
  const [choosing, setChoosing] = useState(false);
  const label = usePresence(on || choosing, LABEL_EXIT_MS);
  const [menuOpen, setMenuOpen] = useState(false);
  const [custom, setCustom] = useState("");

  const uiEndonym =
    LANGUAGE_ENDONYMS[i18n.language as keyof typeof LANGUAGE_ENDONYMS] ?? null;
  const recentOnly = recent.filter((r) => !UI_LANGUAGES.includes(r));

  const pick = (l: string) => {
    setLanguage(l);
    setMenuOpen(false);
    if (choosing) {
      setChoosing(false);
      if (!enabled) toggle(paperId);
    }
  };

  const onMenuOpenChange = (open: boolean) => {
    setMenuOpen(open);
    if (!open) setChoosing(false); // dismissed without a pick: stay off
  };

  const onCustomKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter" || e.nativeEvent.isComposing) return;
    e.preventDefault();
    if (!custom.trim()) return;
    pick(custom);
    setCustom("");
  };

  const item = (l: string) => {
    const selected = l === language;
    return (
      <button
        key={l}
        type="button"
        aria-pressed={selected}
        onClick={() => pick(l)}
        className={cn(
          "flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm outline-none hover:bg-muted focus-visible:bg-muted",
          selected && "bg-accent hover:bg-accent",
        )}
      >
        <span className="truncate">
          {l}
          {l === uiEndonym && (
            <span className="ml-1.5 text-xs text-muted-foreground">
              {t("menu.uiLanguage", "Interface language")}
            </span>
          )}
        </span>
        {selected && <Check className="h-3.5 w-3.5 shrink-0" aria-hidden />}
      </button>
    );
  };

  const sectionLabel = (text: string) => (
    <div className="px-2 pt-1.5 pb-0.5 text-xs text-muted-foreground">
      {text}
    </div>
  );

  return (
    <div className="pointer-events-none absolute right-4 bottom-4 z-20">
      <TooltipProvider>
        <Tooltip disabled={!hasReason}>
          <TooltipTrigger
            render={
              <div
                tabIndex={disabled ? 0 : undefined}
                // Disabled: the container is the focusable control so the
                // reason is reachable by keyboard and screen readers.
                role={disabled ? "button" : undefined}
                aria-disabled={disabled || undefined}
                aria-label={disabled ? pillName : undefined}
                aria-describedby={hasReason ? reasonId : undefined}
                aria-busy={(pending && !hasReason) || undefined}
                onPointerEnter={hasReason ? onDisabledInteract : undefined}
                onFocus={hasReason ? onDisabledInteract : undefined}
                data-menu-open={menuOpen || undefined}
                className={cn(
                  "pointer-events-auto flex h-9 flex-row-reverse items-center overflow-hidden rounded-full bg-popover text-popover-foreground whitespace-nowrap",
                  "shadow-[0_0_16px_2px_color-mix(in_oklab,var(--ring)_35%,transparent),0_4px_12px_rgb(0_0_0/0.12)]",
                  "opacity-55 transition-[opacity,background-color] duration-200 motion-reduce:transition-none",
                  "hover:opacity-100 focus-within:opacity-100 focus-visible:opacity-100 data-[menu-open]:opacity-100 focus-visible:outline-none",
                  on && "bg-accent",
                )}
              />
            }
          >
            <button
              type="button"
              aria-label={pillName}
              aria-hidden={disabled || undefined}
              tabIndex={disabled ? -1 : undefined}
              aria-pressed={on}
              disabled={disabled}
              onClick={() => {
                setMenuOpen(false);
                if (choosing) {
                  setChoosing(false);
                } else if (!enabled && mustChoose) {
                  setChoosing(true);
                  setMenuOpen(true);
                } else {
                  toggle(paperId);
                }
              }}
              className={cn(
                "flex h-9 w-9 shrink-0 items-center justify-center rounded-full border-l border-transparent outline-none transition-[border-color,border-radius] duration-200 hover:bg-muted focus-visible:bg-muted disabled:pointer-events-none disabled:text-muted-foreground motion-reduce:transition-none",
                // The divider and square edge follow the label's open state,
                // so they fade in and out with it instead of popping.
                label.mounted &&
                  label.state === "open" &&
                  "rounded-l-none border-border",
              )}
            >
              <Languages className="h-4 w-4" aria-hidden />
            </button>

            {label.mounted && (
              <div
                data-state={label.state}
                // Transitions (not keyframes) so a reopen during the exit
                // reverses from the current width; @starting-style gives the
                // first-mount grow.
                className={cn(
                  "grid transition-[grid-template-columns,opacity] motion-reduce:transition-none",
                  "data-[state=open]:grid-cols-[1fr] data-[state=open]:opacity-100 data-[state=open]:duration-300 data-[state=open]:ease-[cubic-bezier(0.3,0.7,0.2,1)]",
                  "starting:data-[state=open]:grid-cols-[0fr] starting:data-[state=open]:opacity-0",
                  "data-[state=closed]:grid-cols-[0fr] data-[state=closed]:opacity-0 data-[state=closed]:duration-200 data-[state=closed]:ease-in",
                )}
              >
                <div className="min-w-0 overflow-hidden">
                  <Popover open={menuOpen} onOpenChange={onMenuOpenChange}>
                    <PopoverTrigger
                      render={
                        <button
                          type="button"
                          aria-label={t("pill.language", {
                            language,
                            defaultValue: "Translation language: {{language}}",
                          })}
                          // Closing the label must not leave a focusable ghost.
                          tabIndex={label.state === "open" ? 0 : -1}
                          className="flex h-9 max-w-56 items-center gap-1 rounded-l-full pr-2 pl-3 text-xs outline-none hover:bg-muted focus-visible:bg-muted"
                        />
                      }
                    >
                      <span className="truncate">{language}</span>
                      <ChevronDown className="h-3.5 w-3.5 shrink-0" aria-hidden />
                    </PopoverTrigger>
                    <PopoverContent
                      side="top"
                      align="end"
                      sideOffset={8}
                      className="w-64 origin-(--transform-origin) p-1 duration-150 motion-reduce:animate-none"
                    >
                      {sectionLabel(t("menu.heading", "Translate into"))}
                      {UI_LANGUAGES.map(item)}
                      {recentOnly.length > 0 && (
                        <>
                          {sectionLabel(t("menu.recent", "Recent"))}
                          {recentOnly.map(item)}
                        </>
                      )}
                      <div className="-mx-1 my-1 border-t border-border" />
                      {sectionLabel(
                        t("menu.custom", "Custom (any language, press Enter)"),
                      )}
                      <div className="px-1 pb-1">
                        <Input
                          value={custom}
                          onChange={(e) => setCustom(e.target.value)}
                          onKeyDown={onCustomKey}
                          placeholder={t(
                            "menu.customPlaceholder",
                            "Any language, e.g. 繁體中文（香港）",
                          )}
                          className="h-8 text-sm"
                        />
                      </div>
                      {staleTerminology && (
                        <>
                          <div className="-mx-1 my-1 border-t border-border" />
                          <button
                            type="button"
                            onClick={() => {
                              setMenuOpen(false);
                              onRetranslate();
                            }}
                            className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm outline-none hover:bg-muted focus-visible:bg-muted"
                          >
                            <RefreshCw className="h-3.5 w-3.5 shrink-0" aria-hidden />
                            {t(
                              "menu.retranslate",
                              "Re-translate with current terminology",
                            )}
                          </button>
                        </>
                      )}
                    </PopoverContent>
                  </Popover>
                </div>
              </div>
            )}
            {hasReason && (
              <span id={reasonId} className="sr-only">
                {reasonText}
              </span>
            )}
          </TooltipTrigger>
          <TooltipContent side="top" align="end">
            {reasonText}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
