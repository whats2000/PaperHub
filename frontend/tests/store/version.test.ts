import { afterEach, describe, expect, it, vi } from "vitest";
import type { VersionInfo } from "@/types/domain";

const { getVersionMock } = vi.hoisted(() => ({
  getVersionMock: vi.fn<() => Promise<VersionInfo>>(),
}));
vi.mock("@/lib/api", () => ({ getVersion: getVersionMock }));

import { useVersionStore } from "@/store/version";

afterEach(() => {
  getVersionMock.mockReset();
  useVersionStore.setState({ info: null, changelogOpen: false });
});

describe("version store", () => {
  it("fetchVersion stores the payload", async () => {
    getVersionMock.mockResolvedValue({
      current: "2.37.0",
      latest: "2.38.0",
      update_available: true,
      html_url: "x",
      checked_at: "y",
    });
    await useVersionStore.getState().fetchVersion();
    expect(useVersionStore.getState().info?.update_available).toBe(true);
  });

  it("fetchVersion swallows errors (info stays null)", async () => {
    getVersionMock.mockRejectedValue(new Error("offline"));
    await useVersionStore.getState().fetchVersion();
    expect(useVersionStore.getState().info).toBeNull();
  });

  it("open/close toggles the changelog modal", () => {
    useVersionStore.getState().openChangelog();
    expect(useVersionStore.getState().changelogOpen).toBe(true);
    useVersionStore.getState().closeChangelog();
    expect(useVersionStore.getState().changelogOpen).toBe(false);
  });
});
