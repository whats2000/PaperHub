import { beforeEach, describe, expect, it } from "vitest";

import {
  NOTES_DEFAULT_WIDTH,
  NOTES_MIN_WIDTH,
  SIDE_BY_SIDE_MIN,
  rightPanelLayout,
  useNotesPaneStore,
} from "./notesPane";

beforeEach(() => {
  localStorage.clear();
  useNotesPaneStore.setState({ open: false, activeTab: "reader", notesWidth: NOTES_DEFAULT_WIDTH });
});

describe("useNotesPaneStore", () => {
  it("starts closed at the default width", () => {
    expect(NOTES_DEFAULT_WIDTH).toBe(420);
    expect(NOTES_MIN_WIDTH).toBe(320);
    expect(SIDE_BY_SIDE_MIN).toBe(760);
    const s = useNotesPaneStore.getState();
    expect(s.open).toBe(false);
    expect(s.notesWidth).toBe(420);
  });

  it("toggle opens on the Notes tab, then closes", () => {
    useNotesPaneStore.getState().toggle();
    expect(useNotesPaneStore.getState()).toMatchObject({ open: true, activeTab: "notes" });
    useNotesPaneStore.getState().toggle();
    expect(useNotesPaneStore.getState().open).toBe(false);
  });

  it("close and setActiveTab", () => {
    useNotesPaneStore.getState().toggle();
    useNotesPaneStore.getState().setActiveTab("reader");
    expect(useNotesPaneStore.getState().activeTab).toBe("reader");
    useNotesPaneStore.getState().close();
    expect(useNotesPaneStore.getState().open).toBe(false);
  });

  it("clamps the width to the minimum", () => {
    useNotesPaneStore.getState().setNotesWidth(100);
    expect(useNotesPaneStore.getState().notesWidth).toBe(320);
    useNotesPaneStore.getState().setNotesWidth(512);
    expect(useNotesPaneStore.getState().notesWidth).toBe(512);
  });

  it("remembers the width, not the open state, across reloads", () => {
    useNotesPaneStore.getState().toggle();
    useNotesPaneStore.getState().setNotesWidth(500);
    const saved = JSON.parse(localStorage.getItem("paperhub-notes-pane-v1") ?? "{}") as {
      state?: unknown;
    };
    expect(saved.state).toEqual({ notesWidth: 500, activeTab: "notes" });
  });

  it("opening the pane never resets the dragged width", () => {
    useNotesPaneStore.getState().setNotesWidth(560);
    useNotesPaneStore.getState().toggle();
    expect(useNotesPaneStore.getState().notesWidth).toBe(560);
  });
});

describe("rightPanelLayout", () => {
  const base = { primaryOpen: true, notesOpen: true, notesWidth: 420 };

  it("shows one pane alone as single", () => {
    expect(rightPanelLayout({ ...base, notesOpen: false, panelWidth: 900 }).layout).toBe("single");
    expect(rightPanelLayout({ ...base, primaryOpen: false, panelWidth: 900 }).layout).toBe("single");
  });

  it("uses tabs below 760 px and side by side from 760 px", () => {
    expect(rightPanelLayout({ ...base, panelWidth: 759 }).layout).toBe("tabs");
    expect(rightPanelLayout({ ...base, panelWidth: 760 }).layout).toBe("side");
  });

  it("keeps the Notes width while the reader takes the rest", () => {
    expect(rightPanelLayout({ ...base, panelWidth: 1000 }).notesWidth).toBe(420);
  });

  it("leaves the reader its minimum when Notes is wider than fits", () => {
    expect(rightPanelLayout({ ...base, notesWidth: 600, panelWidth: 800 }).notesWidth).toBe(480);
  });
});
