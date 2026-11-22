/** A chat's papers shown but not in it: the dashed tabs both the reader
 * and the notes pane draw, one of them at most temporary. */
import { beforeEach, describe, expect, it } from "vitest";

import { useCanvasStore } from "./canvas";

const CHAT = 1;
const tabs = (chat = CHAT) => useCanvasStore.getState().outsideByChat[chat] ?? [];

beforeEach(() => {
  useCanvasStore.setState({
    open: false,
    outsideByChat: {},
    closedTab: null,
    requestedPaperId: null,
    paperRequestNonce: 0,
  });
});

describe("outside tabs", () => {
  it("a paper opened by looking is temporary; the next one replaces it", () => {
    useCanvasStore.getState().showOutside(CHAT, 9);
    expect(tabs()).toEqual([{ paperId: 9, preview: true }]);
    useCanvasStore.getState().showOutside(CHAT, 10);
    expect(tabs()).toEqual([{ paperId: 10, preview: true }]);
  });

  it("never more than one temporary tab; a kept tab stays beside it", () => {
    const s = useCanvasStore.getState();
    s.showOutside(CHAT, 9);
    s.keepOutside(CHAT, 9);
    s.showOutside(CHAT, 10);
    s.showOutside(CHAT, 11);
    expect(tabs()).toEqual([
      { paperId: 9, preview: false },
      { paperId: 11, preview: true },
    ]);
  });

  it("showing a paper that already has a tab changes nothing", () => {
    const s = useCanvasStore.getState();
    s.showOutside(CHAT, 9);
    s.keepOutside(CHAT, 9);
    s.showOutside(CHAT, 10);
    s.showOutside(CHAT, 9);
    expect(tabs()).toEqual([
      { paperId: 9, preview: false },
      { paperId: 10, preview: true },
    ]);
  });

  it("closing a tab drops it and names the paper both panes land on", () => {
    const s = useCanvasStore.getState();
    s.showOutside(CHAT, 9);
    s.closeOutside(CHAT, 9, 7);
    expect(tabs()).toEqual([]);
    expect(useCanvasStore.getState().closedTab).toMatchObject({ chat: CHAT, paperId: 9, neighbour: 7 });
  });

  it("each chat has its own tabs; a temporary tab does not survive a chat switch", () => {
    const s = useCanvasStore.getState();
    s.showOutside(CHAT, 9);
    s.keepOutside(CHAT, 9);
    s.showOutside(CHAT, 10);
    s.showOutside(2, 11);
    s.resetForSessionChange();
    expect(tabs()).toEqual([{ paperId: 9, preview: false }]);
    expect(tabs(2)).toEqual([]);
  });

  it("selectPaper switches the reader's paper without opening it", () => {
    useCanvasStore.getState().selectPaper(7);
    const st = useCanvasStore.getState();
    expect(st.open).toBe(false);
    expect(st.requestedPaperId).toBe(7);
    expect(st.paperRequestNonce).toBe(1);
  });
});
