import { ShieldX } from "lucide-react";
import { Trans, useTranslation } from "react-i18next";

import type { OutboundBlockedData } from "@/types/domain";

/** "Not sent: <text> contains a sensitive term (<term>)." — a search the
 *  checkpoint refused (H5a). Muted; enters with fade + zoom from the left. */
export function OutboundBlockedLine({ data }: { data: OutboundBlockedData }) {
  const { t } = useTranslation("chat");
  const q = <span className="font-mono text-[11.5px] text-foreground" />;
  return (
    <p
      data-testid="outbound-blocked"
      className="not-prose mt-2 flex origin-top-left items-start gap-1.5 text-xs text-muted-foreground animate-in fade-in-0 zoom-in-95 duration-200 motion-reduce:animate-none"
    >
      <ShieldX className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
      {data.term ? (
        <span>
          <Trans
            t={t}
            i18nKey="outbound.blocked.term"
            defaults="Not sent: <q>{{text}}</q> contains a sensitive term (<b>{{term}}</b>)."
            values={{ text: data.text, term: data.term }}
            components={{ q, b: <b className="font-medium text-foreground" /> }}
          />
        </span>
      ) : (
        <span>
          <Trans
            t={t}
            i18nKey="outbound.blocked.reason"
            defaults="Not sent: <q>{{text}}</q>. {{reason}}"
            values={{ text: data.text, reason: data.reason }}
            components={{ q }}
          />
        </span>
      )}
    </p>
  );
}
