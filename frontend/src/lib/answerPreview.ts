import { splitProposals } from "@/lib/proposals";

/**
 * Make an assistant answer readable in a clamped preview: a fenced code block
 * (the `library_stats` answers end with a ```sql block, which a line clamp
 * cuts to a bare "```sql…") becomes one inline `code` span, and an agent
 * answer's `<proposal>` lines (shown as buttons in chat) are dropped.
 */
export function cleanForPreview(text: string): string {
  return (
    splitProposals(text).body
      // A closed fence, on its own lines or all on one line.
      .replace(/```[a-zA-Z0-9_-]*[ \t]*\n?([\s\S]*?)```/g, (_m, code: string) => {
        const one = code.replace(/\s+/g, " ").trim();
        return one ? `\`${one}\`` : "";
      })
      // An unclosed one: keep its body, drop the fence marker.
      .replace(/```[a-zA-Z0-9_-]*[ \t]*\n?/g, "")
      .replace(/\n{3,}/g, "\n\n")
      .trim()
  );
}
