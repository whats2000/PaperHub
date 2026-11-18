import type { EditorState } from "@tiptap/pm/state";
import { describe, expect, it } from "vitest";

import { skipFolded } from "@/components/notes/partsDecorations";

describe("skipFolded", () => {
  it("reads nothing of the document when nothing is folded or hidden (it runs on every transaction)", () => {
    const state = {
      selection: { head: 5 },
      get doc(): never {
        throw new Error("the document was read");
      },
    } as unknown as EditorState;
    expect(skipFolded(state, 3, { focusOn: true, activePart: "p1", folded: new Set() })).toBeNull();
    expect(() => skipFolded(state, 3, { focusOn: false, activePart: null, folded: new Set(["d1"]) })).toThrow();
  });
});
