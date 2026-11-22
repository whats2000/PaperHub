import { describe, expect, it } from "vitest";

import {
  caretFoldOwner,
  foldOwners,
  foldSummaryOf,
  foldersHiding,
  hiddenByFolds,
  treeFromRows,
  type FoldNode,
} from "./folds";

const n = (id: string, type = "paragraph", props: Record<string, unknown> = {}, children: FoldNode[] = []): FoldNode => ({
  id,
  type,
  props,
  children,
});
const h = (id: string, level: number) => n(id, "heading", { level });
const li = (id: string, children: FoldNode[] = []) => n(id, "bulletListItem", {}, children);
const ids = (list: FoldNode[] | undefined) => (list ?? []).map((b) => b.id);

describe("foldOwners", () => {
  it("a heading folds to the next heading of the same or a higher level", () => {
    const tree = [h("h1", 1), h("h2", 2), n("p1"), h("h3", 3), n("p2"), h("h2b", 2), n("p3"), h("h1b", 1), n("p4")];
    const owners = foldOwners(tree);
    expect(ids(owners.get("h1"))).toEqual(["h2", "p1", "h3", "p2", "h2b", "p3"]);
    expect(ids(owners.get("h2"))).toEqual(["p1", "h3", "p2"]);
    expect(ids(owners.get("h3"))).toEqual(["p2"]);
    expect(ids(owners.get("h1b"))).toEqual(["p4"]);
  });

  it("a paper § section folds like an H1: to the next section or H1", () => {
    const tree = [n("s1", "section"), n("q1", "paperQuote"), h("h2", 2), n("p"), n("s2", "section"), n("q2", "paperQuote"), h("h1", 1), n("p2")];
    const owners = foldOwners(tree);
    expect(ids(owners.get("s1"))).toEqual(["q1", "h2", "p"]);
    expect(ids(owners.get("s2"))).toEqual(["q2"]);
    // An H2 stops at a section, as at an H1.
    expect(ids(owners.get("h2"))).toEqual(["p"]);
  });

  it("a list item folds its nested items; one without any folds nothing", () => {
    const tree = [li("a", [li("a1", [li("a11")]), li("a2")]), li("b")];
    const owners = foldOwners(tree);
    expect(ids(owners.get("a"))).toEqual(["a1", "a2"]);
    expect(ids(owners.get("a1"))).toEqual(["a11"]);
    expect(owners.has("b")).toBe(false);
    expect(owners.has("a2")).toBe(false);
  });

  it("numbered and to-do items fold their nested items too", () => {
    const tree = [n("o", "numberedListItem", {}, [n("o1", "numberedListItem")]), n("c", "checkListItem", {}, [n("c1")])];
    const owners = foldOwners(tree);
    expect(ids(owners.get("o"))).toEqual(["o1"]);
    expect(ids(owners.get("c"))).toEqual(["c1"]);
  });

  it("a divider folds nothing, and a heading folds past it", () => {
    const tree = [h("h", 2), n("p1"), n("d", "divider"), n("p2")];
    const owners = foldOwners(tree);
    expect(owners.has("d")).toBe(false);
    expect(ids(owners.get("h"))).toEqual(["p1", "d", "p2"]);
  });

  it("a heading's own nested blocks fold with it", () => {
    const tree = [n("h", "heading", { level: 2 }, [n("c")]), n("p"), h("k", 2)];
    expect(ids(foldOwners(tree).get("h"))).toEqual(["c", "p"]);
  });

  it("a heading with nothing under it is not foldable", () => {
    const owners = foldOwners([h("a", 2), h("b", 2), n("p")]);
    expect(owners.has("a")).toBe(false);
    expect(owners.has("b")).toBe(true);
  });
});

describe("hiddenByFolds", () => {
  it("hides each folded owner's range (blocks nested in it go with it)", () => {
    const tree = [h("h", 1), li("a", [li("a1")]), n("p"), h("k", 1), n("q")];
    expect([...hiddenByFolds(tree, new Set(["h"]))].sort()).toEqual(["a", "p"]);
    expect([...hiddenByFolds(tree, new Set(["a"]))]).toEqual(["a1"]);
    expect(hiddenByFolds(tree, new Set(["gone"])).size).toBe(0);
  });
});

describe("foldSummaryOf", () => {
  it("counts the folded blocks (nested ones too) and the marks they quote once each", () => {
    const range = [n("q1", "paperQuote", { markId: "m1" }), li("a", [n("q2", "paperQuote", { markId: "m2" })]), n("q3", "paperQuote", { markId: "m1" })];
    expect(foldSummaryOf(range)).toEqual({ blocks: 4, marks: 2 });
  });
});

describe("foldersHiding", () => {
  it("names every folded owner a block sits under, outermost first", () => {
    const tree = [h("h", 1), li("a", [li("a1", [li("a11")])]), h("k", 1), n("q")];
    expect(foldersHiding(tree, "a11", new Set(["h", "a1", "k"]))).toEqual(["h", "a1"]);
    expect(foldersHiding(tree, "q", new Set(["h"]))).toEqual([]);
    expect(foldersHiding(tree, "h", new Set(["h"]))).toEqual([]);
  });
});

describe("caretFoldOwner", () => {
  it("is the block itself when it folds, else the innermost fold holding it", () => {
    const tree = [h("h", 1), h("h2", 2), n("p"), li("a", [li("a1")]), n("d", "divider"), n("x")];
    expect(caretFoldOwner(tree, "h2")).toBe("h2");
    expect(caretFoldOwner(tree, "p")).toBe("h2");
    expect(caretFoldOwner(tree, "a1")).toBe("a");
    expect(caretFoldOwner(tree, "x")).toBe("h2");
    expect(caretFoldOwner([n("lone")], "lone")).toBeNull();
  });
});

describe("treeFromRows", () => {
  it("builds the tree from stored rows (parent ids, document order)", () => {
    const tree = treeFromRows([
      { id: "a", parent_id: null, type: "bulletListItem", props: {} },
      { id: "a1", parent_id: "a", type: "bulletListItem", props: {} },
      { id: "b", parent_id: null, type: "paragraph", props: {} },
    ]);
    expect(tree.map((x) => [x.id, ids(x.children)])).toEqual([
      ["a", ["a1"]],
      ["b", []],
    ]);
  });
});
