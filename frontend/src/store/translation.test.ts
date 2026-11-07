import { beforeEach, describe, expect, it, vi } from "vitest";
import i18n from "@/lib/i18n";
import { defaultLanguage, useTranslationStore } from "@/store/translation";

beforeEach(() => {
  useTranslationStore.setState({
    language: "English",
    recent: [],
    enabledByPaper: {},
    hintSeen: false,
  });
});

describe("translation store", () => {
  it("defaults the language to the UI locale endonym", () => {
    const prev = i18n.language;
    void i18n.changeLanguage("zh-TW");
    expect(defaultLanguage()).toBe("繁體中文");
    void i18n.changeLanguage("ja");
    expect(defaultLanguage()).toBe("日本語");
    void i18n.changeLanguage(prev);
  });

  it("dedupes trimmed recent entries, most recent first", () => {
    const { setLanguage } = useTranslationStore.getState();
    setLanguage("日本語");
    setLanguage("Deutsch");
    setLanguage("  日本語 ");
    const s = useTranslationStore.getState();
    expect(s.language).toBe("日本語");
    expect(s.recent).toEqual(["日本語", "Deutsch"]);
  });

  it("caps recent at 5", () => {
    const { setLanguage } = useTranslationStore.getState();
    for (const l of ["a", "b", "c", "d", "e", "f"]) setLanguage(l);
    expect(useTranslationStore.getState().recent).toEqual(["f", "e", "d", "c", "b"]);
  });

  it("ignores blank languages", () => {
    useTranslationStore.getState().setLanguage("   ");
    expect(useTranslationStore.getState().language).toBe("English");
    expect(useTranslationStore.getState().recent).toEqual([]);
  });

  it("remembers the first-use hint in persisted state", () => {
    useTranslationStore.getState().markHintSeen();
    expect(useTranslationStore.getState().hintSeen).toBe(true);
    const saved = JSON.parse(localStorage.getItem("paperhub-translation-v1") ?? "{}") as {
      state?: { hintSeen?: boolean };
    };
    expect(saved.state?.hintSeen).toBe(true);
  });

  it("toggles per paper", () => {
    const { toggle } = useTranslationStore.getState();
    toggle(3);
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(true);
    toggle(3);
    expect(useTranslationStore.getState().enabledByPaper[3]).toBe(false);
  });

  it("survives throwing localStorage and a malformed persisted recent", () => {
    const setSpy = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    const getSpy = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(() => useTranslationStore.getState().setLanguage("Deutsch")).not.toThrow();
    setSpy.mockRestore();
    getSpy.mockRestore();
    useTranslationStore.setState({ recent: "oops" as unknown as string[] });
    useTranslationStore.getState().setLanguage("日本語");
    expect(useTranslationStore.getState().recent).toEqual(["日本語"]);
  });
});
