/**
 * The core agent marks follow-up suggestions as `<proposal>…</proposal>`
 * lines at the end of its answer (ruling R12). The stored answer keeps them;
 * the chat strips them from the displayed markdown and renders each as a
 * button. Used by the live stream and the replay alike.
 */
export interface SplitAnswer {
  /** The answer without any proposal markup. */
  body: string;
  /** Proposal texts, in order, trimmed and non-empty. */
  proposals: string[];
}

const PROPOSAL = /<proposal>([\s\S]*?)(?:<\/proposal>|$)/g;
// While streaming, the opening tag may arrive in pieces ("<prop"): hide a
// partial tag at the start of the last line so it never flashes.
const PARTIAL_TAG = /(^|\n)[ \t]*<(?:p(?:r(?:o(?:p(?:o(?:s(?:a(?:l)?)?)?)?)?)?)?)?$/;

export function splitProposals(content: string): SplitAnswer {
  const proposals: string[] = [];
  let complete = true;
  const stripped = content.replace(PROPOSAL, (match: string, text: string) => {
    if (!match.endsWith("</proposal>")) complete = false;
    const t = text.trim();
    if (t && complete) proposals.push(t);
    return "";
  });
  const body = stripped
    .replace(PARTIAL_TAG, "$1")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
  return { body, proposals };
}
