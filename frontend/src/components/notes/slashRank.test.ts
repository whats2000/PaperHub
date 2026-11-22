import { describe, expect, it } from "vitest";
import { rankSlashItems } from "./slashRank";

const item = (title: string, group: string, aliases: string[] = []) => ({
  title,
  group,
  aliases,
  onItemClick: () => {},
});

const items = [
  item("Paragraph", "Basic blocks", ["p"]),
  item("Table of contents", "Basic blocks", ["toc", "contents", "目錄"]),
  item("Image", "Advanced", ["picture"]),
  item("Table", "Advanced", ["table", "表格"]),
];

describe("rankSlashItems", () => {
  it("puts the item the query names exactly first, so Enter on /table inserts a table", () => {
    const out = rankSlashItems(items, "table");
    expect(out.map((i) => i.title)).toEqual(["Table", "Table of contents"]);
  });

  it("keeps each group together when it moves its group up", () => {
    const out = rankSlashItems(
      [...items, item("Table grid", "Advanced", ["grid"])],
      "table",
    );
    expect(out.map((i) => i.group)).toEqual(["Advanced", "Advanced", "Basic blocks"]);
    expect(out[0]!.title).toBe("Table");
  });

  it("matches an alias exactly in any script", () => {
    expect(rankSlashItems(items, "目錄")[0]!.title).toBe("Table of contents");
  });

  it("leaves the order alone with no exact match", () => {
    expect(rankSlashItems(items, "ta").map((i) => i.title)).toEqual(["Table of contents", "Table"]);
    expect(rankSlashItems(items, "").map((i) => i.title)).toEqual(items.map((i) => i.title));
  });
});
