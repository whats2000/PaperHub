import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { TranslatePill } from "./TranslatePill";
import { useTranslationStore } from "@/store/translation";

const PAPER = 7;

function renderPill(
  props: Partial<React.ComponentProps<typeof TranslatePill>> = {},
) {
  return render(
    <TranslatePill
      paperId={PAPER}
      disabledReason={null}
      staleTerminology={false}
      onRetranslate={() => {}}
      {...props}
    />,
  );
}

const toggleName = /^translate paper$/i;

describe("TranslatePill", () => {
  beforeEach(() => {
    useTranslationStore.setState({
      language: "English",
      recent: [],
      enabledByPaper: {},
    });
  });

  it("is off by default: no label, the icon button has an accessible name", () => {
    renderPill();
    const icon = screen.getByRole("button", { name: toggleName });
    expect(icon).toHaveAttribute("aria-pressed", "false");
    expect(
      screen.queryByRole("button", { name: /translation language/i }),
    ).not.toBeInTheDocument();
  });

  it("turns on when clicked; the label appears and the icon node stays the same", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ recent: ["English"] });
    renderPill();
    const icon = screen.getByRole("button", { name: toggleName });
    await user.click(icon);
    expect(useTranslationStore.getState().enabledByPaper[PAPER]).toBe(true);
    const label = screen.getByRole("button", { name: /translation language/i });
    expect(label).toHaveTextContent("English");
    expect(screen.getByRole("button", { name: toggleName })).toBe(icon);
    expect(icon).toHaveAttribute("aria-pressed", "true");
  });

  it("English UI, never picked: the first toggle-on opens the menu instead of translating", async () => {
    const user = userEvent.setup();
    renderPill();
    const icon = screen.getByRole("button", { name: toggleName });
    await user.click(icon);
    // Not on yet (no English→English translation), but the menu is open.
    expect(useTranslationStore.getState().enabledByPaper[PAPER]).toBeFalsy();
    expect(icon).toHaveAttribute("aria-pressed", "false");
    await screen.findByPlaceholderText(/any language/i);
    await user.click(screen.getByRole("button", { name: /^日本語/ }));
    const s = useTranslationStore.getState();
    expect(s.language).toBe("日本語");
    expect(s.enabledByPaper[PAPER]).toBe(true);
    expect(icon).toHaveAttribute("aria-pressed", "true");
  });

  it("closing that first menu without a pick leaves translation off", async () => {
    const user = userEvent.setup();
    renderPill();
    await user.click(screen.getByRole("button", { name: toggleName }));
    const label = await screen.findByRole("button", { name: /translation language/i });
    await screen.findByPlaceholderText(/any language/i);
    await user.keyboard("{Escape}");
    expect(useTranslationStore.getState().enabledByPaper[PAPER]).toBeFalsy();
    // The label animates out, then unmounts.
    await waitFor(() => expect(label).not.toBeInTheDocument());
  });

  it("a non-English default starts translating on the first toggle-on", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ language: "繁體中文" });
    renderPill();
    await user.click(screen.getByRole("button", { name: toggleName }));
    expect(useTranslationStore.getState().enabledByPaper[PAPER]).toBe(true);
  });

  it("animates the label out before unmounting it when turned off", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ enabledByPaper: { [PAPER]: true } });
    renderPill();
    const label = screen.getByRole("button", { name: /translation language/i });
    await user.click(screen.getByRole("button", { name: toggleName }));
    // Still mounted while the exit animation plays, then removed.
    expect(label).toBeInTheDocument();
    expect(label.closest("[data-state]")).toHaveAttribute("data-state", "closed");
    await waitFor(() => expect(label).not.toBeInTheDocument());
  });

  it("sets a trimmed free-text language and adds it to recent", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ enabledByPaper: { [PAPER]: true } });
    renderPill();
    await user.click(
      screen.getByRole("button", { name: /translation language/i }),
    );
    const input = await screen.findByPlaceholderText(/any language/i);
    await user.type(input, "  日本語（関西） {Enter}");
    const s = useTranslationStore.getState();
    expect(s.language).toBe("日本語（関西）");
    expect(s.recent[0]).toBe("日本語（関西）");
  });

  it("picks a UI language from the menu and marks the current one selected", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ enabledByPaper: { [PAPER]: true } });
    renderPill();
    await user.click(
      screen.getByRole("button", { name: /translation language/i }),
    );
    const current = await screen.findByRole("button", { name: /^English/ });
    expect(current).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByRole("button", { name: /^日本語/ }));
    expect(useTranslationStore.getState().language).toBe("日本語");
  });

  it("shows the re-translate item only when terminology is stale", async () => {
    const user = userEvent.setup();
    const onRetranslate = vi.fn();
    useTranslationStore.setState({ enabledByPaper: { [PAPER]: true } });
    const { rerender } = renderPill({ onRetranslate });
    await user.click(
      screen.getByRole("button", { name: /translation language/i }),
    );
    await screen.findByPlaceholderText(/any language/i);
    expect(
      screen.queryByRole("button", { name: /re-translate/i }),
    ).not.toBeInTheDocument();

    rerender(
      <TranslatePill
        paperId={PAPER}
        disabledReason={null}
        staleTerminology
        onRetranslate={onRetranslate}
      />,
    );
    await user.click(screen.getByRole("button", { name: /re-translate/i }));
    expect(onRetranslate).toHaveBeenCalledOnce();
  });

  it("when disabled, exposes a named, described control and does not toggle", async () => {
    const user = userEvent.setup();
    renderPill({ disabledReason: "no_blocks" });
    const pill = screen.getByRole("button", { name: toggleName });
    expect(pill).toHaveAttribute("aria-disabled", "true");
    expect(pill).toHaveAccessibleDescription(
      "This PDF has no paragraph layout yet",
    );
    await user.tab(); // the disabled pill itself is focusable
    expect(pill).toHaveFocus();
    // The tooltip shows the same reason (sr-only copy + tooltip popup).
    await waitFor(() =>
      expect(
        screen.getAllByText("This PDF has no paragraph layout yet"),
      ).toHaveLength(2),
    );
    await user.click(pill);
    await user.keyboard("{Enter}");
    expect(useTranslationStore.getState().enabledByPaper[PAPER]).toBeFalsy();
    expect(
      screen.queryByRole("button", { name: /translation language/i }),
    ).not.toBeInTheDocument();
  });

  it("describes the inline-math reason", () => {
    renderPill({ disabledReason: "no_inline_math_llm" });
    expect(
      screen.getByRole("button", { name: toggleName }),
    ).toHaveAccessibleDescription(
      "Formulas in this PDF couldn't be read; translation is off. Set a Marker LLM model and re-process this PDF.",
    );
  });

  it("closes the menu on Escape and returns focus to the label", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ enabledByPaper: { [PAPER]: true } });
    renderPill();
    const label = screen.getByRole("button", { name: /translation language/i });
    await user.click(label);
    await screen.findByPlaceholderText(/any language/i);
    await user.keyboard("{Escape}");
    await waitFor(() =>
      expect(
        screen.queryByPlaceholderText(/any language/i),
      ).not.toBeInTheDocument(),
    );
    expect(label).toHaveFocus();
  });

  it("reopening during the exit reuses the same label node (no restart)", async () => {
    const user = userEvent.setup();
    useTranslationStore.setState({ enabledByPaper: { [PAPER]: true } });
    renderPill();
    const icon = screen.getByRole("button", { name: toggleName });
    const label = screen.getByRole("button", { name: /translation language/i });
    const wrap = label.closest("[data-state]");
    await user.click(icon); // off: exit starts
    expect(wrap).toHaveAttribute("data-state", "closed");
    await user.click(icon); // on again within the exit window
    expect(wrap).toHaveAttribute("data-state", "open");
    expect(
      screen.getByRole("button", { name: /translation language/i }),
    ).toBe(label);
  });
});
