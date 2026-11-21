import { cn } from "@/lib/utils";

import type { TocRow } from "./useTocRows";

const INDENT_PX = 16;

/** The entries of a table of contents, indented by level; a new entry opens
 * and a removed one closes in place. Shared by the `/toc` block and the
 * floating rail. */
export function TocList({
  rows,
  onGo,
  currentId = null,
  disabled = false,
}: {
  rows: TocRow[];
  onGo: (blockId: string) => void;
  /** The entry being read (the rail's scroll spy). */
  currentId?: string | null;
  /** Not reachable by Tab (a closed rail list). */
  disabled?: boolean;
}) {
  return (
    <ul className="m-0 list-none p-0">
      {rows.map((e) => (
        <li
          key={e.blockId}
          data-state={e.state}
          aria-hidden={e.state === "out" ? true : undefined}
          className="grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none"
          style={{
            gridTemplateRows: e.state === "open" ? "1fr" : "0fr",
            opacity: e.state === "open" ? 1 : 0,
          }}
        >
          <div className="min-h-0 overflow-hidden" style={{ paddingLeft: (e.level - 1) * INDENT_PX }}>
            <button
              type="button"
              tabIndex={e.state === "out" || disabled ? -1 : 0}
              aria-current={e.blockId === currentId ? "location" : undefined}
              onClick={() => onGo(e.blockId)}
              className={cn(
                "w-full truncate rounded-sm py-[2px] text-left text-[13px] text-muted-foreground underline-offset-2 transition-colors duration-150 hover:bg-accent hover:text-foreground hover:underline motion-reduce:transition-none",
                e.blockId === currentId && "font-medium text-foreground",
              )}
            >
              {e.text}
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}
