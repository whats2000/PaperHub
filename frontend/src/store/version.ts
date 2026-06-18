import { create } from "zustand";
import type { VersionInfo } from "@/types/domain";
import { getVersion } from "@/lib/api";

interface VersionState {
  info: VersionInfo | null;
  changelogOpen: boolean;
  /** Fetch /version once; swallows errors so a missing/offline backend never
   *  surfaces a toast or breaks the menu. */
  fetchVersion: () => Promise<void>;
  openChangelog: () => void;
  closeChangelog: () => void;
}

export const useVersionStore = create<VersionState>((set) => ({
  info: null,
  changelogOpen: false,
  fetchVersion: async () => {
    try {
      const info = await getVersion();
      set({ info });
    } catch {
      // Self-hosted: an unreachable backend or disabled check is normal.
    }
  },
  openChangelog: () => set({ changelogOpen: true }),
  closeChangelog: () => set({ changelogOpen: false }),
}));
