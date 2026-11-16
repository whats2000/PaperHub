/** The one changed stretch between two texts, for a tracked change: what is
 * kept before and after it, what goes and what comes in. Ends never cut a
 * Latin word or a number in two ("83%" → "89%" is one change, not "3" → "9");
 * other scripts (CJK) diff by character. */
export interface TextDiff {
  prefix: string;
  del: string;
  ins: string;
  suffix: string;
}

const WORD = /[A-Za-z0-9À-ɏ_]/;

const isWord = (ch: string | undefined) => ch !== undefined && WORD.test(ch);

export function diffText(before: string, after: string): TextDiff {
  const max = Math.min(before.length, after.length);
  let p = 0;
  while (p < max && before[p] === after[p]) p++;
  let s = 0;
  while (s < max - p && before[before.length - 1 - s] === after[after.length - 1 - s]) s++;
  // Back off to a word boundary on each side.
  while (p > 0 && isWord(before[p - 1]) && (isWord(before[p]) || isWord(after[p]))) p--;
  while (
    s > 0 &&
    isWord(before[before.length - s]) &&
    (isWord(before[before.length - s - 1]) || isWord(after[after.length - s - 1]))
  ) {
    s--;
  }
  return {
    prefix: before.slice(0, p),
    del: before.slice(p, before.length - s),
    ins: after.slice(p, after.length - s),
    suffix: before.slice(before.length - s),
  };
}
