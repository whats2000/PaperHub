import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StringListField } from "./StringListField";

describe("StringListField", () => {
  it("shows existing items as chips", () => {
    render(<StringListField id="t" label="Terms" value="Atlas, Orion" onChange={vi.fn()} />);
    expect(screen.getByText("Atlas")).toBeInTheDocument();
    expect(screen.getByText("Orion")).toBeInTheDocument();
  });

  it("adds on Enter and sends one joined list", async () => {
    const onChange = vi.fn();
    render(<StringListField id="t" label="Terms" value="Atlas" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Terms"), "Orion{Enter}");
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("Atlas, Orion");
  });

  it("adds on comma", async () => {
    const onChange = vi.fn();
    render(<StringListField id="t" label="Terms" value="" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Terms"), "Zed,");
    expect(onChange).toHaveBeenCalledWith("Zed");
  });

  it("ignores a duplicate (case-insensitive)", async () => {
    const onChange = vi.fn();
    render(<StringListField id="t" label="Terms" value="Atlas" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Terms"), "atlas{Enter}");
    expect(onChange).not.toHaveBeenCalled();
  });

  it("removes with the x button and sends one joined list", async () => {
    const onChange = vi.fn();
    render(<StringListField id="t" label="Terms" value="Atlas, Orion" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove Atlas" }));
    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange).toHaveBeenCalledWith("Orion");
  });

  it("is inert when locked", () => {
    render(<StringListField id="t" label="Terms" value="Atlas" locked onChange={vi.fn()} />);
    expect(screen.getByLabelText("Terms")).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Remove Atlas" })).not.toBeInTheDocument();
  });
});

describe("StringListField failure handling", () => {
  it("rolls a failed add back to the saved value", async () => {
    const onChange = vi.fn().mockRejectedValue(new Error("API 403"));
    render(<StringListField id="t" label="Terms" value="Atlas" onChange={onChange} />);
    await userEvent.type(screen.getByLabelText("Terms"), "Orion{Enter}");
    expect(onChange).toHaveBeenCalledWith("Atlas, Orion");
    await waitFor(() => expect(screen.queryByText("Orion")).not.toBeInTheDocument());
    expect(screen.getByText("Atlas")).toBeInTheDocument();
  });

  it("brings a chip back when its removal fails to save", async () => {
    const onChange = vi.fn().mockRejectedValue(new Error("network"));
    render(<StringListField id="t" label="Terms" value="Atlas, Orion" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove Atlas" }));
    await waitFor(() => expect(screen.getAllByText("Atlas")).toHaveLength(1));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Remove Atlas" })).toBeInTheDocument(),
    );
  });

  it("remove then re-add within the leave window keeps one chip and no key warning", async () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const onChange = vi.fn().mockResolvedValue(undefined);
    render(<StringListField id="t" label="Terms" value="Atlas" onChange={onChange} />);
    await userEvent.click(screen.getByRole("button", { name: "Remove Atlas" }));
    await userEvent.type(screen.getByLabelText("Terms"), "Atlas{Enter}");
    await waitFor(() => expect(screen.getAllByText("Atlas")).toHaveLength(1));
    expect(err).not.toHaveBeenCalled();
    err.mockRestore();
  });
});
