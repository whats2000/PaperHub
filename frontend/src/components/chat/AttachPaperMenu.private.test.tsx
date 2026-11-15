import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AttachPaperMenu } from "@/components/chat/AttachPaperMenu";
import { useChatStore } from "@/store/chat";
import { useSettingsStore } from "@/store/settings";

const { uploadPdfMock } = vi.hoisted(() => ({ uploadPdfMock: vi.fn() }));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  uploadPdf: uploadPdfMock,
}));

vi.mock("sonner", () => ({
  toast: {
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
    loading: vi.fn(() => "toast-id"),
  },
}));

const settingsWith = (value: string) => ({
  categories: [
    {
      key: "privacy_outbound",
      label: "Privacy & outbound",
      fields: [
        {
          key: "PAPERHUB_PRIVATE_UPLOAD_DEFAULT",
          label: "New uploads are private",
          type: "bool" as const,
          value,
          secret: false,
          restart_required: false,
        },
      ],
    },
  ],
});

const realFetchConfig = useSettingsStore.getState().fetchConfig;

beforeEach(() => {
  useSettingsStore.setState({ config: settingsWith("0"), fetchConfig: realFetchConfig });
  uploadPdfMock.mockReset();
  uploadPdfMock.mockResolvedValue({
    papers_id: 42,
    paper_content_id: 100,
    cache_hit: false,
    title: "Secret Paper",
    is_private: false,
  });
  useChatStore.setState({
    sessions: [
      { id: 1, title: "S", messages: [], backend_session_id: 7 },
    ],
    activeSessionId: 1,
    _nextId: 2,
    referencesBySession: {},
    composerDraft: "",
  });
});

const pdf = () =>
  new File([new Uint8Array([0x25])], "p.pdf", { type: "application/pdf" });

async function openMenu() {
  await userEvent.click(screen.getByRole("button", { name: /attach paper/i }));
}

describe("AttachPaperMenu private checkbox", () => {
  it("is unchecked by default and is sent when ticked", async () => {
    render(<AttachPaperMenu />);
    await openMenu();
    const box = screen.getByRole("checkbox", { name: "Private document" });
    expect(box).not.toBeChecked();
    expect(box).toHaveAccessibleDescription(
      "Marks it as a confidential document — shown with a lock.",
    );
    expect(
      screen.getByText("Marks it as a confidential document — shown with a lock."),
    ).toBeInTheDocument();

    await userEvent.click(box);
    expect(box).toBeChecked();
    uploadPdfMock.mockResolvedValueOnce({
      papers_id: 42,
      paper_content_id: 100,
      cache_hit: false,
      title: "Secret Paper",
      is_private: true,
    });
    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());

    await waitFor(() => expect(uploadPdfMock).toHaveBeenCalledTimes(1));
    expect(uploadPdfMock).toHaveBeenCalledWith(7, expect.any(File), {
      private: true,
    });
    expect(useChatStore.getState().referencesBySession[7]?.[0]?.is_private).toBe(
      true,
    );
  });

  it("sends no private flag when left unticked", async () => {
    render(<AttachPaperMenu />);
    await openMenu();
    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());
    await waitFor(() => expect(uploadPdfMock).toHaveBeenCalledTimes(1));
    expect(uploadPdfMock).toHaveBeenLastCalledWith(7, expect.any(File), {
      private: false,
    });
  });

  it("unticks after a failed private upload so the next upload is not private", async () => {
    render(<AttachPaperMenu />);
    await openMenu();
    uploadPdfMock.mockRejectedValueOnce(new Error("boom"));
    await userEvent.click(screen.getByRole("checkbox", { name: "Private document" }));
    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());
    await waitFor(() => expect(uploadPdfMock).toHaveBeenCalledTimes(1));
    expect(uploadPdfMock).toHaveBeenLastCalledWith(7, expect.any(File), {
      private: true,
    });
    // The popover stays open after a failure.
    await waitFor(() =>
      expect(screen.getByRole("checkbox", { name: "Private document" })).not.toBeChecked(),
    );

    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());
    await waitFor(() => expect(uploadPdfMock).toHaveBeenCalledTimes(2));
    expect(uploadPdfMock).toHaveBeenLastCalledWith(7, expect.any(File), {
      private: false,
    });
  });
});

describe("AttachPaperMenu server truth", () => {
  it("uses the server's is_private for the local reference, not the checkbox", async () => {
    uploadPdfMock.mockResolvedValueOnce({
      papers_id: 42,
      paper_content_id: 100,
      cache_hit: true,
      title: "Already Private",
      is_private: true,
    });
    render(<AttachPaperMenu />);
    await openMenu();
    // Checkbox left unticked: the server says the PDF is already private.
    await userEvent.upload(screen.getByLabelText("PDF file"), pdf());
    await waitFor(() => expect(uploadPdfMock).toHaveBeenCalledTimes(1));
    expect(uploadPdfMock).toHaveBeenCalledWith(7, expect.any(File), {
      private: false,
    });
    expect(useChatStore.getState().referencesBySession[7]?.[0]?.is_private).toBe(
      true,
    );
  });
});

describe("AttachPaperMenu private default from settings", () => {
  it("starts checked when PAPERHUB_PRIVATE_UPLOAD_DEFAULT is on", async () => {
    useSettingsStore.setState({
      config: {
        categories: [
          {
            key: "privacy_outbound",
            label: "Privacy & outbound",
            fields: [
              {
                key: "PAPERHUB_PRIVATE_UPLOAD_DEFAULT",
                label: "New uploads are private",
                type: "bool",
                value: "1",
                secret: false,
                restart_required: false,
              },
            ],
          },
        ],
      },
    });
    render(<AttachPaperMenu />);
    await openMenu();
    expect(screen.getByRole("checkbox", { name: "Private document" })).toBeChecked();
    useSettingsStore.setState({ config: null });
  });
});

describe("AttachPaperMenu private default when settings cannot be read", () => {
  it("retries once, then fails closed (checked) and lets the user untick", async () => {
    const getSpy = vi.fn();
    const fetchConfig = vi.fn(() => {
      getSpy();
      return Promise.resolve();
    });
    useSettingsStore.setState({ config: null, fetchConfig });
    render(<AttachPaperMenu />);
    await openMenu();
    const box = screen.getByRole("checkbox", { name: "Private document" });
    await waitFor(() => expect(box).toBeChecked());
    expect(getSpy).toHaveBeenCalledTimes(2);
    await userEvent.click(box);
    expect(box).not.toBeChecked();
  });
});

describe("AttachPaperMenu private default while settings are loading", () => {
  it("shows checked while the fetch is in flight, then follows the loaded default", async () => {
    let resolve: () => void = () => undefined;
    const fetchConfig = vi.fn(
      () =>
        new Promise<void>((r) => {
          resolve = r;
        }),
    );
    useSettingsStore.setState({ config: null, fetchConfig });
    render(<AttachPaperMenu />);
    await openMenu();
    const box = screen.getByRole("checkbox", { name: "Private document" });
    expect(box).toBeChecked();
    useSettingsStore.setState({ config: settingsWith("0") });
    resolve();
    await waitFor(() => expect(box).not.toBeChecked());
  });
});
