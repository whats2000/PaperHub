import type { ChangelogEntry } from "@/types/domain";
import data from "@/changelog/changelog.json";

export const CHANGELOG: ChangelogEntry[] = data;

/** Highlights for a locale, falling back to `en` when the locale is absent. */
export function localizedHighlights(entry: ChangelogEntry, lng: string): string[] {
  return entry.highlights[lng] ?? entry.highlights.en ?? [];
}
