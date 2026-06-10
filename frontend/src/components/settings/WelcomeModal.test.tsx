import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { SettingsReadiness } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";
import { WelcomeModal } from "./WelcomeModal";

function readiness(ready: boolean): SettingsReadiness {
  return {
    ready,
    credentials_set: ready,
    models: {
      small: { model: "gemini/x", key_ok: ready, missing_keys: ready ? [] : ["GEMINI_API_KEY"] },
      flagship: { model: "gemini/y", key_ok: ready, missing_keys: ready ? [] : ["GEMINI_API_KEY"] },
    },
  };
}

describe("WelcomeModal", () => {
  beforeEach(() =>
    useSettingsStore.setState({ readiness: null, welcomeDismissed: false, isOpen: false }),
  );
  afterEach(() =>
    useSettingsStore.setState({ readiness: null, welcomeDismissed: false, isOpen: false }),
  );

  it("shows the tour with a checklist while not ready", () => {
    useSettingsStore.setState({ readiness: readiness(false) });
    render(<WelcomeModal />);
    expect(screen.getByText(/Welcome to PaperHub/i)).toBeInTheDocument();
    expect(screen.getByText(/Add a provider API key/i)).toBeInTheDocument();
    expect(screen.getByText(/Set a valid Flagship-tier model/i)).toBeInTheDocument();
  });

  it("stays hidden once ready", () => {
    useSettingsStore.setState({ readiness: readiness(true) });
    render(<WelcomeModal />);
    expect(screen.queryByText(/Welcome to PaperHub/i)).not.toBeInTheDocument();
  });

  it("Open Settings dismisses the tour and opens settings", async () => {
    useSettingsStore.setState({ readiness: readiness(false) });
    render(<WelcomeModal />);
    await userEvent.click(screen.getByText(/Open Settings/i));
    expect(useSettingsStore.getState().welcomeDismissed).toBe(true);
    expect(useSettingsStore.getState().isOpen).toBe(true);
  });
});
