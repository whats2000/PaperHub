/** The bodies of the four paper reference blocks (quote, figure, equation,
 * region), each inside the shared `PaperRefFrame`. */
import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { Image, ScanText, Sigma, Sparkles, SquareDashed, TextQuote } from "lucide-react";
import "katex/dist/katex.min.css";

import { renderDisplayMath } from "@/lib/displayMath";
import { joinedQuote } from "@/lib/marks/pieces";

import { CropImage } from "./CropImage";
import { markPieces, type MarkProps } from "./markProps";
import { PaperRefFrame } from "./PaperRefFrame";

interface ViewProps {
  id: string;
  props: MarkProps;
}

export function PaperQuoteView({ id, props }: ViewProps) {
  const { t } = useTranslation();
  // Several pieces: "…" for words skipped between strokes, a paragraph
  // break within one stroke.
  const pieces = markPieces(props);
  const quote = pieces.length > 1 ? joinedQuote(pieces) || props.quote : props.quote;
  return (
    <PaperRefFrame
      blockId={id}
      props={props}
      icon={<TextQuote className="size-3 shrink-0" aria-hidden />}
      label={t("notes:ref.fromPaper", "From the paper")}
    >
      {/* A stroke across paragraphs keeps its paragraph breaks. */}
      <blockquote className="m-0 whitespace-pre-line font-paper text-[14px] leading-normal text-muted-foreground">
        {quote}
      </blockquote>
    </PaperRefFrame>
  );
}

export function PaperFigureView({ id, props }: ViewProps) {
  const { t } = useTranslation();
  const piece = markPieces(props)[0];
  return (
    <PaperRefFrame
      blockId={id}
      props={props}
      icon={<Image className="size-3 shrink-0" aria-hidden />}
      label={t("notes:ref.figure", "Figure from the paper")}
    >
      <CropImage piece={piece} alt={props.quote} />
      {props.quote && (
        <div className="mt-1 font-paper text-[12.5px] text-muted-foreground">{props.quote}</div>
      )}
    </PaperRefFrame>
  );
}

export function PaperEquationView({ id, props }: ViewProps) {
  const { t } = useTranslation();
  const piece = markPieces(props)[0];
  const latex = typeof piece?.latex === "string" ? piece.latex : "";
  // The HTML reader's LaTeX is the paper's own source; a PDF's is Marker's
  // transcription, to check against the crop.
  const transcribed = latex !== "" && piece?.latex_from !== "source";
  const html = useMemo(() => renderDisplayMath(latex), [latex]);
  return (
    <PaperRefFrame
      blockId={id}
      props={props}
      icon={<Sigma className="size-3 shrink-0" aria-hidden />}
      label={t("notes:ref.equation", "Equation from the paper")}
      tagExtra={
        transcribed ? (
          <span className="ml-1 inline-flex items-center gap-0.5">
            <Sparkles className="size-3" aria-hidden />
            {t("notes:ref.latexByModel", "LaTeX transcribed by the model · check it against the page")}
          </span>
        ) : null
      }
    >
      <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] items-center gap-2">
        {html ? (
          <div className="overflow-x-auto py-1" dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <div className="font-paper text-[13px] text-muted-foreground">{props.quote}</div>
        )}
        <CropImage piece={piece} alt={latex || props.quote} />
      </div>
    </PaperRefFrame>
  );
}

export function PaperRegionView({ id, props }: ViewProps) {
  const { t } = useTranslation();
  const piece = markPieces(props)[0];
  return (
    <PaperRefFrame
      blockId={id}
      props={props}
      icon={<SquareDashed className="size-3 shrink-0" aria-hidden />}
      label={t("notes:ref.region", "Region of the paper")}
      tagExtra={
        <span className="ml-1 inline-flex items-center gap-0.5">
          <ScanText className="size-3" aria-hidden />
          {piece?.ocr
            ? t("notes:ref.ocrText", "OCR text · check numbers against the crop")
            : t("notes:ref.layerText", "Text under the box, from the page's text layer")}
        </span>
      }
    >
      <div className="grid grid-cols-[repeat(auto-fit,minmax(170px,1fr))] items-start gap-2">
        <div className="font-paper text-[13px] whitespace-pre-wrap text-muted-foreground">
          {props.quote}
        </div>
        <CropImage piece={piece} alt={props.quote} />
      </div>
    </PaperRefFrame>
  );
}
