import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  BookOpen,
  BrainCircuit,
  Mic,
  Presentation,
  Columns2,
  Send,
  Square,
} from "lucide-react";

import { SlideContextChip } from "@/components/chat/SlideContextChip";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";
import { useChatStore } from "@/store/chat";
import { useCanvasStore } from "@/store/canvas";
import { createSpeechRecognizer, isSpeechSupported, type SpeechRecognizer } from "@/lib/speech";

interface Props {
  onSubmit: (text: string) => void;
  disabled: boolean;
  /** First-run gate: show a "finish setup" hint and treat the composer as
   *  locked (the parent also folds this into `disabled`). */
  setupRequired?: boolean;
  /** Open the Settings modal from the setup hint. */
  onOpenSettings?: () => void;
  /** Whether the Memory Manager panel is currently open. */
  memoryOpen?: boolean;
  /** Called when the user clicks the Memory button to toggle the panel. */
  onToggleMemory?: () => void;
  /** Called when the user clicks the References button to toggle the canvas.
   *  When provided, overrides the default internal toggleCanvas behaviour so
   *  the parent (ChatPage) can close the Memory panel before opening the Canvas
   *  — ensuring mutual exclusivity of the shared right-panel slot. */
  onToggleCanvas?: () => void;
  /** Whether the Citation Canvas is currently open (controls aria-pressed). */
  canvasOpen?: boolean;
  /** Whether the Slides panel is currently open. */
  slidesOpen?: boolean;
  /** Called when the user clicks the Slides button to toggle the panel. */
  onToggleSlides?: () => void;
  /** When the session has a deck in view, the active-slide chip state; null
   *  hides the chip. Content (page) tracks the active slide; attached is the
   *  sticky toggle. */
  slideChip?: { page: number; attached: boolean; onToggle: () => void } | null;
  /** Whether the assistant is currently streaming a response. When true, the
   *  Send button is replaced by a Stop button so the user can cancel the turn. */
  isStreaming?: boolean;
  /** Called when the user clicks the Stop button during streaming. */
  onStop?: () => void;
}

interface Capability {
  icon: typeof BookOpen;
  /** i18n key under chat:composer for the label + tooltip. */
  labelKey: string;
  tooltipKey: string;
}

const CAPABILITIES: Capability[] = [
  {
    icon: Columns2,
    labelKey: "composer.compare",
    tooltipKey: "composer.compareTooltip",
  },
];

export function Composer({
  onSubmit,
  disabled,
  setupRequired = false,
  onOpenSettings,
  memoryOpen = false,
  onToggleMemory,
  onToggleCanvas,
  canvasOpen: canvasOpenProp,
  slidesOpen = false,
  onToggleSlides,
  slideChip = null,
  isStreaming = false,
  onStop,
}: Props) {
  const { t } = useTranslation("chat");
  const draft = useChatStore((s) => s.composerDraft);
  const setDraft = useChatStore((s) => s.setComposerDraft);
  const focusSeq = useChatStore((s) => s.composerFocusSeq);
  const toggleCanvasStore = useCanvasStore((s) => s.toggleCanvas);
  const canvasOpenStore = useCanvasStore((s) => s.open);
  const ref = useRef<HTMLTextAreaElement>(null);

  const [listening, setListening] = useState(false);
  const recognizerRef = useRef<SpeechRecognizer | null>(null);
  const baseDraftRef = useRef("");
  const speechSupported = isSpeechSupported();

  const toggleVoice = () => {
    if (listening) {
      recognizerRef.current?.stop();
      recognizerRef.current = null;
      return;
    }
    // Guard a rapid double-click: the ref updates synchronously, so a second
    // call in the same tick (before listening re-renders) bails here.
    if (recognizerRef.current) return;
    baseDraftRef.current = value;
    const rec = createSpeechRecognizer({
      onInterim: (text) => {
        // onInterim delivers the FULL session transcript each event (see
        // createSpeechRecognizer), so REPLACE the base draft — never append.
        const base = baseDraftRef.current;
        setValue(base && text ? `${base} ${text}` : base || text);
      },
      onEnd: () => {
        recognizerRef.current = null;
        setListening(false);
      },
      onError: () => {
        recognizerRef.current = null;
        setListening(false);
      },
    });
    if (!rec) return;
    recognizerRef.current = rec;
    rec.start();
    setListening(true);
  };

  // Stop an in-flight recognizer if the composer unmounts mid-dictation (e.g. a
  // session switch) so the mic doesn't stay open writing to the draft store.
  useEffect(() => () => recognizerRef.current?.stop(), []);

  // If the parent provides a canvas toggle handler + open state, use those
  // (so ChatPage can enforce mutual exclusivity with Memory). Otherwise fall
  // back to the store values for backwards-compat (Composer.canvas.test).
  const handleToggleCanvas = onToggleCanvas ?? toggleCanvasStore;
  const canvasOpen = canvasOpenProp ?? canvasOpenStore;

  const value = draft;
  const setValue = setDraft;

  // When something prefills the composer (deck-chip Generate/Edit), focus the
  // textarea and drop the cursor at the end so the prompt is ready to complete.
  // Skip the initial render (focusSeq starts at 0) to avoid stealing focus.
  useEffect(() => {
    if (focusSeq === 0) return;
    const el = ref.current;
    if (!el) return;
    el.focus();
    const end = el.value.length;
    el.setSelectionRange(end, end);
  }, [focusSeq]);

  const submit = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    // Stop dictation on send so a live recognizer doesn't keep writing partial
    // transcripts into the just-cleared composer.
    if (listening) {
      recognizerRef.current?.stop();
      recognizerRef.current = null;
      setListening(false);
    }
    onSubmit(trimmed);
    setDraft("");
    ref.current?.focus();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    // Plain Enter submits; Shift+Enter allows default (newline).
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <form
      className="shrink-0 border-t border-border bg-card p-3"
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
      <div className="max-w-3xl mx-auto">
        {setupRequired && (
          <div className="mb-2 flex items-center gap-2 rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-200">
            <span className="min-w-0 flex-1">{t("composer.setupRequired")}</span>
            <button
              type="button"
              onClick={onOpenSettings}
              className="shrink-0 rounded font-medium underline underline-offset-2 hover:opacity-80"
            >
              {t("composer.setupCta")}
            </button>
          </div>
        )}
        {/* Single rounded container — textarea on top, tool row + send on bottom.
            focus-within ring unifies the visual treatment across child focus. */}
        <div className="rounded-2xl border border-input bg-background shadow-sm transition-shadow focus-within:ring-2 focus-within:ring-ring">
          {slideChip && (
            <div className="px-3 pt-2">
              <SlideContextChip
                page={slideChip.page}
                attached={slideChip.attached}
                onToggle={slideChip.onToggle}
              />
            </div>
          )}
          <textarea
            ref={ref}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={t("composer.placeholder")}
            rows={2}
            className="block w-full resize-none bg-transparent px-4 pt-3 pb-1 text-sm placeholder:text-muted-foreground focus:outline-none disabled:cursor-not-allowed disabled:opacity-50"
            disabled={disabled}
            aria-label={t("composer.messageAria")}
          />
          <TooltipProvider>
            <div className="flex items-center justify-between gap-1 px-2 pb-2">
              <div className="flex items-center gap-0.5">
                <AttachPaperMenu />
                <Tooltip>
                  <TooltipTrigger
                    render={<span tabIndex={0} className="inline-flex" />}
                  >
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={() => handleToggleCanvas()}
                      aria-pressed={canvasOpen}
                      className={
                        canvasOpen
                          ? "h-8 w-8 bg-accent text-foreground"
                          : "h-8 w-8 text-muted-foreground hover:text-foreground"
                      }
                      aria-label={t("composer.references")}
                    >
                      <BookOpen className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>{t("composer.referencesTooltip")}</p>
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger
                    render={<span tabIndex={0} className="inline-flex" />}
                  >
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={onToggleMemory}
                      aria-pressed={memoryOpen}
                      className={
                        memoryOpen
                          ? "h-8 w-8 bg-accent text-foreground"
                          : "h-8 w-8 text-muted-foreground hover:text-foreground"
                      }
                      aria-label={t("composer.memory")}
                    >
                      <BrainCircuit className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>{t("composer.memoryTooltip")}</p>
                  </TooltipContent>
                </Tooltip>
                <Tooltip>
                  <TooltipTrigger
                    render={<span tabIndex={0} className="inline-flex" />}
                  >
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      onClick={onToggleSlides}
                      aria-pressed={slidesOpen}
                      className={
                        slidesOpen
                          ? "h-8 w-8 bg-accent text-foreground"
                          : "h-8 w-8 text-muted-foreground hover:text-foreground"
                      }
                      aria-label={t("composer.slides")}
                    >
                      <Presentation className="h-4 w-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="top">
                    <p>{t("composer.slidesTooltip")}</p>
                  </TooltipContent>
                </Tooltip>
                {CAPABILITIES.map(({ icon: Icon, labelKey, tooltipKey }) => (
                  <Tooltip key={labelKey}>
                    <TooltipTrigger
                      render={<span tabIndex={0} className="inline-flex" />}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        disabled
                        className="h-8 w-8 pointer-events-none text-muted-foreground"
                        aria-label={t(labelKey)}
                      >
                        <Icon className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      <p>{t(tooltipKey)}</p>
                    </TooltipContent>
                  </Tooltip>
                ))}
              </div>
              {/* Right cluster: voice + send — input actions, set apart from
                  the left-side panel toggles. */}
              <div className="flex items-center gap-1">
                {speechSupported && (
                  <Tooltip>
                    <TooltipTrigger
                      render={<span tabIndex={0} className="inline-flex" />}
                    >
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        onClick={toggleVoice}
                        aria-pressed={listening}
                        className={
                          listening
                            ? "h-8 w-8 bg-accent text-foreground"
                            : "h-8 w-8 text-muted-foreground hover:text-foreground"
                        }
                        aria-label={t("composer.voice")}
                      >
                        <Mic className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      <p>
                        {listening
                          ? t("composer.voiceListening")
                          : t("composer.voiceDictate")}
                      </p>
                    </TooltipContent>
                  </Tooltip>
                )}
                {isStreaming ? (
                  <Tooltip>
                    <TooltipTrigger
                      render={<span tabIndex={0} className="inline-flex" />}
                    >
                      <Button
                        type="button"
                        size="icon"
                        onClick={onStop}
                        aria-label={t("composer.stop")}
                        className="h-8 w-8 rounded-full"
                      >
                        <Square className="h-4 w-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent side="top">
                      <p>{t("composer.stopTooltip")}</p>
                    </TooltipContent>
                  </Tooltip>
                ) : (
                  <Button
                    type="submit"
                    size="icon"
                    disabled={disabled || value.trim().length === 0}
                    aria-label={t("composer.send")}
                    className="h-8 w-8 rounded-full"
                  >
                    <Send className="h-4 w-4" />
                  </Button>
                )}
              </div>
            </div>
          </TooltipProvider>
        </div>
      </div>
    </form>
  );
}
