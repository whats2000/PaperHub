/** The badge of open comment threads whose words are gone (see
 * commentBadges.ts): their count; each click opens the next one. */
import { MessageSquareWarning } from "lucide-react";
import { useRef } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";

import type { Badge } from "./commentBadges";

function LostCommentsBadge({
  ids,
  closing,
  onOpen,
}: {
  ids: string[];
  closing: boolean;
  onOpen: (id: string, el: HTMLElement) => void;
}) {
  const { t } = useTranslation();
  const next = useRef(0);
  return (
    <button
      type="button"
      data-testid="lost-comments-badge"
      data-closed={closing ? "" : undefined}
      aria-label={t("notes:comment.lostBadge", { count: ids.length })}
      title={t("notes:comment.textChanged", "The commented text changed.")}
      onClick={(e) => {
        const id = ids[next.current % ids.length];
        next.current += 1;
        if (id) onOpen(id, e.currentTarget);
      }}
      className="ml-1.5 inline-flex translate-y-[-1px] items-center gap-0.5 rounded-full border border-border bg-muted px-1.5 align-middle text-[11px] leading-[18px] text-muted-foreground transition-[opacity,scale] duration-200 ease-out animate-in fade-in-0 zoom-in-95 hover:text-foreground data-closed:scale-90 data-closed:opacity-0 motion-reduce:animate-none motion-reduce:transition-none"
    >
      <MessageSquareWarning className="size-3" aria-hidden />
      {ids.length}
    </button>
  );
}

/** The badges, as portals into their widget hosts in the editor. */
export function LostCommentBadges({
  badges,
  host,
  onOpen,
}: {
  badges: Record<string, Badge>;
  host: (blockId: string) => HTMLElement;
  onOpen: (id: string, el: HTMLElement) => void;
}) {
  return (
    <>
      {Object.entries(badges).map(([blockId, b]) =>
        createPortal(<LostCommentsBadge ids={b.ids} closing={b.closing} onOpen={onOpen} />, host(blockId), `cbadge:${blockId}`),
      )}
    </>
  );
}
