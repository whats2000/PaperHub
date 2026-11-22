import { filterSuggestionItems } from "@blocknote/core";

type SlashItem = { title: string; aliases?: readonly string[]; group?: string };

/**
 * BlockNote's filter keeps the menu's own order, so "/table" listed
 * "Table of contents" (Basic blocks) above "Table" (Advanced) and Enter
 * inserted a table of contents. The item whose title or alias equals the
 * query moves to the top, with its whole group so no group is shown twice.
 */
export function rankSlashItems<T extends SlashItem>(items: T[], query: string): T[] {
  const found = filterSuggestionItems(items, query);
  const q = query.trim().toLowerCase();
  if (!q) return found;
  const exact = (i: T) =>
    i.title.toLowerCase() === q || (i.aliases ?? []).some((a) => a.toLowerCase() === q);
  if (!found.some(exact)) return found;
  const groups = new Map<string | undefined, T[]>();
  for (const i of found) {
    const list = groups.get(i.group) ?? [];
    list.push(i);
    groups.set(i.group, list);
  }
  const ordered = [...groups.values()].map((list) => [
    ...list.filter(exact),
    ...list.filter((i) => !exact(i)),
  ]);
  ordered.sort((a, b) => Number(exact(b[0]!)) - Number(exact(a[0]!)));
  return ordered.flat();
}
