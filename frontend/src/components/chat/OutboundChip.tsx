import { useId, useState } from "react";
import { ChevronDown, ChevronUp, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { useDestinationLabel } from "@/lib/outboundDestination";
import type { OutboundRewrittenData } from "@/types/domain";

/**
 * "Search sent as <text>": a search the outbound checkpoint rewrote before it
 * left the device (H5a). The chevron opens a detail box (Sent / Written /
 * Why / To) that grows and shrinks by height; the chip itself enters with
 * fade + zoom from its top-left corner.
 */
export function OutboundChip({ data }: { data: OutboundRewrittenData }) {
  const { t } = useTranslation("chat");
  const [open, setOpen] = useState(false);
  const detailId = useId();
  const dest = useDestinationLabel(data.destination);
  const Chevron = open ? ChevronUp : ChevronDown;

  return (
    <div data-testid="outbound-chip" className="not-prose mt-2 flex flex-col">
      <span className="inline-flex max-w-full origin-top-left items-center gap-1.5 self-start rounded-full border border-border bg-muted px-2.5 py-0.5 text-xs text-muted-foreground animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none">
        <ShieldCheck className="h-3.5 w-3.5 shrink-0" aria-hidden />
        <b className="font-medium text-foreground">{t("outbound.chip.title", "Search sent as")}</b>
        <span className="min-w-0 truncate font-mono text-[11.5px] text-foreground">
          {data.sent_text}
        </span>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={detailId}
          title={t("outbound.chip.whatChanged", "What changed")}
          aria-label={t("outbound.chip.whatChanged", "What changed")}
          onClick={() => setOpen((v) => !v)}
          className="inline-flex shrink-0 rounded px-0.5 text-muted-foreground hover:text-foreground"
        >
          <Chevron className="h-3.5 w-3.5" aria-hidden />
        </button>
      </span>
      <div
        id={detailId}
        data-open={open}
        aria-hidden={!open}
        className="grid grid-rows-[0fr] transition-[grid-template-rows] duration-200 ease-out data-[open=true]:grid-rows-[1fr] motion-reduce:transition-none"
      >
        <div className="min-h-0 overflow-hidden">
          <dl className="mt-1.5 grid grid-cols-[max-content_1fr] gap-x-2.5 gap-y-1 rounded-lg border border-border px-2.5 py-2 text-xs">
            <dt className="text-muted-foreground">{t("outbound.chip.sent", "Sent")}</dt>
            <dd className="break-words font-mono">{data.sent_text}</dd>
            <dt className="text-muted-foreground">{t("outbound.chip.written", "Written")}</dt>
            <dd className="break-words font-mono text-muted-foreground">
              <del>{data.original_text}</del>
            </dd>
            <dt className="text-muted-foreground">{t("outbound.chip.why", "Why")}</dt>
            <dd>{data.reason}</dd>
            <dt className="text-muted-foreground">{t("outbound.chip.to", "To")}</dt>
            <dd>{dest}</dd>
          </dl>
        </div>
      </div>
    </div>
  );
}
