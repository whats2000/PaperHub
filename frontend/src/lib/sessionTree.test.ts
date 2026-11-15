import { describe, expect, it } from "vitest";

import { groupSessionsByFork, groupSessionsByTime } from "@/lib/sessionTree";
import type { ChatSession } from "@/types/domain";

function sess(
  id: number,
  backend: number | null,
  forkedFrom: number | null = null,
): ChatSession {
  return {
    id,
    title: `s${id}`,
    messages: [],
    backend_session_id: backend,
    forked_from_session_id: forkedFrom,
  };
}

const ids = (rows: { session: ChatSession }[]) =>
  rows.map((r) => r.session.id);
const forks = (rows: { session: ChatSession; isFork: boolean }[]) =>
  rows.filter((r) => r.isFork).map((r) => r.session.id);

describe("groupSessionsByFork", () => {
  it("keeps a flat list when there are no forks", () => {
    const out = groupSessionsByFork([sess(1, 10), sess(2, 20)]);
    expect(ids(out)).toEqual([1, 2]);
    expect(forks(out)).toEqual([]);
  });

  it("emits a fork directly under its parent, indented", () => {
    // recency: fork(3) is newest, then parent(1), then unrelated(2)
    const out = groupSessionsByFork([
      sess(3, 30, /*forkedFrom*/ 10),
      sess(1, 10),
      sess(2, 20),
    ]);
    // Parent pulls its fork up beneath it; unrelated session stays put.
    expect(ids(out)).toEqual([1, 3, 2]);
    expect(forks(out)).toEqual([3]);
  });

  it("groups multiple forks of the same parent", () => {
    const out = groupSessionsByFork([
      sess(1, 10),
      sess(2, 20, 10),
      sess(3, 30, 10),
    ]);
    expect(ids(out)).toEqual([1, 2, 3]);
    expect(forks(out)).toEqual([2, 3]);
  });

  it("flattens a fork-of-a-fork to one indent level, DFS order", () => {
    // 1 -> 2 -> 3 (fork of a fork); 3 must render at the same indent as 2,
    // right after 2.
    const out = groupSessionsByFork([
      sess(1, 10),
      sess(2, 20, 10),
      sess(3, 30, 20),
    ]);
    expect(ids(out)).toEqual([1, 2, 3]);
    expect(forks(out)).toEqual([2, 3]); // both indented, neither is a root
  });

  it("treats an orphaned fork (missing parent) as a top-level root", () => {
    // parent 99 is not in the list (deleted/tombstoned)
    const out = groupSessionsByFork([sess(2, 20, 99), sess(1, 10)]);
    expect(ids(out)).toEqual([2, 1]);
    expect(forks(out)).toEqual([]); // orphan is NOT indented
  });

  it("emits every session exactly once even with a malformed cycle", () => {
    // 1 <-> 2 cycle (should never happen, but must not loop)
    const out = groupSessionsByFork([sess(1, 10, 20), sess(2, 20, 10)]);
    expect(out.length).toBe(2);
    expect(new Set(ids(out))).toEqual(new Set([1, 2]));
  });
});

describe("groupSessionsByTime", () => {
  // Local time: Saturday 14 November 2026, 15:00.
  const now = new Date(2026, 10, 14, 15, 0, 0);
  const at = (y: number, m: number, d: number, h = 12) => new Date(y, m, d, h).toISOString();
  const timed = (
    id: number,
    backend: number | null,
    updated_at?: string,
    forkedFrom: number | null = null,
  ): ChatSession => ({ ...sess(id, backend, forkedFrom), updated_at });
  const sections = (list: ChatSession[]) =>
    groupSessionsByTime(list, now).map((g) => [g.key, ids(g.rows)]);

  it("buckets roots by activity: today, yesterday, 7 days, 30 days, month, older", () => {
    const out = sections([
      timed(1, 10, at(2026, 10, 14, 9)),
      timed(2, 20, at(2026, 10, 13, 23)),
      timed(3, 30, at(2026, 10, 9)),
      timed(4, 40, at(2026, 9, 20)),
      timed(5, 50, at(2026, 8, 3)),
      timed(6, 60, at(2026, 8, 28)),
      timed(7, 70, at(2025, 11, 31)),
      timed(8, 80),
    ]);
    expect(out).toEqual([
      ["today", [1]],
      ["yesterday", [2]],
      ["d7", [3]],
      ["d30", [4]],
      ["2026-09", [6, 5]],
      ["2025-12", [7]],
      ["older", [8]],
    ]);
  });

  it("starts today at local midnight", () => {
    const out = sections([timed(1, 10, at(2026, 10, 14, 0)), timed(2, 20, at(2026, 10, 13, 23))]);
    expect(out).toEqual([
      ["today", [1]],
      ["yesterday", [2]],
    ]);
  });

  it("orders roots newest first, a root's activity being the latest of it and its forks", () => {
    const out = groupSessionsByTime(
      [
        timed(1, 10, at(2026, 10, 14, 8)),
        timed(2, 20, at(2026, 10, 14, 9)),
        timed(3, 30, at(2026, 10, 14, 11), 10), // fork of 1, the newest activity
      ],
      now,
    );
    expect(out).toHaveLength(1);
    expect(ids(out[0]!.rows)).toEqual([1, 3, 2]);
    expect(forks(out[0]!.rows)).toEqual([3]);
  });

  it("keeps a fork under its parent even when the fork's own day differs", () => {
    const out = groupSessionsByTime(
      [timed(1, 10, at(2026, 10, 14, 8)), timed(2, 20, at(2026, 10, 1), 10)],
      now,
    );
    expect(out.map((g) => g.key)).toEqual(["today"]);
    expect(ids(out[0]!.rows)).toEqual([1, 2]);
  });

  it("puts untimestamped roots after timestamped ones, in incoming order", () => {
    const out = groupSessionsByTime(
      [timed(1, 10), timed(2, 20, at(2026, 10, 14, 8)), timed(3, 30)],
      now,
    );
    expect(out.map((g) => [g.key, ids(g.rows)])).toEqual([
      ["today", [2]],
      ["older", [1, 3]],
    ]);
  });

  it("gives a month section its first day", () => {
    const out = groupSessionsByTime([timed(1, 10, at(2026, 8, 3))], now);
    expect(out[0]!.month?.getFullYear()).toBe(2026);
    expect(out[0]!.month?.getMonth()).toBe(8);
  });
});
