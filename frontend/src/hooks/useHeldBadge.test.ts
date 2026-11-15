import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useHeldBadge } from "@/hooks/useHeldBadge";
import { getHeldSearches, getPausedCards, type HeldSearch } from "@/lib/api";
import { useNavStore } from "@/store/nav";

vi.mock("@/lib/api", () => ({ getHeldSearches: vi.fn(), getPausedCards: vi.fn() }));
const list = vi.mocked(getHeldSearches);
const paused = vi.mocked(getPausedCards);

const held = (id: string, session_id: number | null, unattended = false): HeldSearch => ({
  id,
  created_at: "2026-11-13T01:00:00+00:00",
  caller: "paper_search",
  kind: "search",
  destination: "api.semanticscholar.org",
  text: "q",
  reason: null,
  unattended,
  run_id: null,
  session_id,
});
const rows = (n: number): HeldSearch[] => Array.from({ length: n }, (_, i) => held(`h${i}`, null, true));

beforeEach(() => {
  vi.useFakeTimers();
  list.mockReset();
  paused.mockReset();
  paused.mockResolvedValue([]);
  useNavStore.setState({
    heldBadge: 0, heldBySession: {}, pausedActionsBySession: {}, heldRefreshSeq: 0,
  });
});
afterEach(() => vi.useRealTimers());

describe("useHeldBadge", () => {
  it("sets the badge from the held list on mount", async () => {
    list.mockResolvedValue(rows(2));
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(useNavStore.getState().heldBadge).toBe(2);
  });

  it("counts the pending searches of each chat", async () => {
    list.mockResolvedValue([held("a", 7), held("b", 7), held("c", 9), held("d", 9, true), held("e", null)]);
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(useNavStore.getState().heldBadge).toBe(5);
    // Only searches decided in a chat (not unattended background ones).
    expect(useNavStore.getState().heldBySession).toEqual({ 7: 2, 9: 1 });
  });

  it("counts the Ask-first actions paused runs wait on, per chat", async () => {
    list.mockResolvedValue([]);
    paused.mockResolvedValue([
      { session_id: 7, run_id: 1, kind: "action", id: "a1" },
      { session_id: 7, run_id: 1, kind: "held", id: "h1" },
      { session_id: 9, run_id: 2, kind: "action", id: "a2" },
    ]);
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    // Held rows are counted from the held list, not twice.
    expect(useNavStore.getState().pausedActionsBySession).toEqual({ 7: 1, 9: 1 });
  });

  it("polls every 30 s", async () => {
    list.mockResolvedValue(rows(1));
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    list.mockResolvedValue(rows(4));
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(useNavStore.getState().heldBadge).toBe(4);
  });

  it("refreshes when the window regains focus", async () => {
    list.mockResolvedValue([]);
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    list.mockResolvedValue(rows(3));
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(useNavStore.getState().heldBadge).toBe(3);
  });

  it("refreshes when something asks for it (refreshHeld)", async () => {
    list.mockResolvedValue([held("a", 7)]);
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    list.mockResolvedValue([]);
    await act(async () => {
      useNavStore.getState().refreshHeld();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(useNavStore.getState().heldBadge).toBe(0);
    expect(useNavStore.getState().heldBySession).toEqual({});
  });

  it("keeps the last count when a request fails", async () => {
    list.mockResolvedValueOnce(rows(2));
    renderHook(() => useHeldBadge());
    await act(() => vi.advanceTimersByTimeAsync(0));
    list.mockRejectedValue(new Error("down"));
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(useNavStore.getState().heldBadge).toBe(2);
  });
});
