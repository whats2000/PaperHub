import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { Composer } from "@/components/chat/Composer";
import { useCanvasStore } from "@/store/canvas";

beforeEach(() =>
  useCanvasStore.setState({ open: false, requestedChunkId: null, requestNonce: 0 }),
);

describe("Composer References button", () => {
  it("is enabled and toggles the canvas open/closed", async () => {
    render(<Composer onSubmit={() => {}} disabled={false} />);
    const btn = screen.getByRole("button", { name: /^references$/i });
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect(useCanvasStore.getState().open).toBe(true);
    await userEvent.click(btn);
    expect(useCanvasStore.getState().open).toBe(false);
  });
});
