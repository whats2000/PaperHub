import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

import { API_BASE_URL } from "../../lib/api";
import { useSettingsStore } from "../../store/settings";
import { SettingsModal } from "./SettingsModal";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({
  toast: { error: toastError, success: vi.fn(), info: vi.fn(), loading: vi.fn() },
}));

const f = (key: string, label: string, type: string, value: string, extra = {}) => ({
  key,
  label,
  type,
  value,
  secret: false,
  restart_required: false,
  locked: false,
  ...extra,
});

const privacyFields = () => [
  f("PAPERHUB_OUTBOUND_REVIEW", "Review outbound searches", "bool", "1", { locked: true }),
  f("PAPERHUB_OUTBOUND_UNSURE", "When the review is not sure", "enum", "ask", {
    choices: ["ask", "block"],
    locked: true,
  }),
  f("PAPERHUB_SENSITIVE_TERMS", "Sensitive terms", "string_list", "Atlas"),
  f("PAPERHUB_PRIVATE_UPLOAD_DEFAULT", "New uploads are private", "bool", "0"),
  f("PAPERHUB_TRUSTED_SEARCH_URL", "Trusted search service", "string", "https://s.example"),
  f("PAPERHUB_PUBLIC_WEB_SEARCH", "Public web search", "bool", "0"),
  f("PAPERHUB_OUTBOUND_ALLOWLIST", "Allowed destinations", "string_list", "*", {
    locked: true,
  }),
  f("PAPERHUB_OUTBOUND_LOG_RETENTION_DAYS", "Keep entries for", "int", "365", {
    locked: true,
    min: 7,
  }),
  f("PAPERHUB_OUTBOUND_REVIEW_MODEL", "Review model", "string", "m", {
    advanced: true,
    locked: true,
  }),
  f("PAPERHUB_OUTBOUND_EXTRA_FLAG", "Extra outbound flag", "bool", "1", {
    advanced: true,
    locked: true,
  }),
  f("PAPERHUB_OUTBOUND_REVIEW_TIMEOUT", "Review timeout", "int", "20", { advanced: true }),
];

let fields = privacyFields();
let nonLocal: { key: string; model: string }[] = [];
let patches: Record<string, string | null>[] = [];
let patchStatus = 200;
let probes = 0;
let reachable = true;

const server = setupServer(
  http.get(`${API_BASE_URL}/settings`, () =>
    HttpResponse.json({
      categories: [
        { key: "privacy_outbound", label: "Privacy & outbound", fields },
        {
          key: "models_providers",
          label: "Models & providers",
          fields: [f("PAPERHUB_MODEL_SMALL", "Small model (backend text)", "string", "gemini/x")],
        },
      ],
    }),
  ),
  http.get(`${API_BASE_URL}/settings/model-options`, () =>
    HttpResponse.json({ providers: [], options: {} }),
  ),
  http.get(`${API_BASE_URL}/settings/readiness`, () =>
    HttpResponse.json({
      ready: true,
      credentials_set: true,
      models: {
        small: { model: "m", key_ok: true, missing_keys: [], error: null, detail: null },
        flagship: { model: "m", key_ok: true, missing_keys: [], error: null, detail: null },
      },
      non_local: nonLocal,
    }),
  ),
  http.get(`${API_BASE_URL}/outbound/destinations`, () =>
    HttpResponse.json([
      { host: "export.arxiv.org", source: "paper_source" },
      { host: "search.lab.internal", source: "trusted_search" },
    ]),
  ),
  http.get(`${API_BASE_URL}/outbound/trusted-search/status`, () => {
    probes += 1;
    return HttpResponse.json(reachable ? { reachable: true, http_status: 200 } : { reachable: false, error: "ConnectError" });
  }),
  http.patch(`${API_BASE_URL}/settings`, async ({ request }) => {
    patches.push((await request.json()) as Record<string, string | null>);
    if (patchStatus !== 200) return new HttpResponse('{"detail":"x"}', { status: patchStatus });
    return HttpResponse.json({ updated: [], cleared: [], restart_required: [] });
  }),
);

describe("SettingsModal privacy pane", () => {
  beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
  afterEach(() => server.resetHandlers());
  afterAll(() => server.close());
  beforeEach(() => {
    fields = privacyFields();
    nonLocal = [{ key: "PAPERHUB_MODEL_SMALL", model: "gemini/gemini-3.5-flash" }];
    patches = [];
    patchStatus = 200;
    probes = 0;
    reachable = true;
    toastError.mockClear();
    useSettingsStore.setState({
      isOpen: true,
      config: null,
      restartPending: [],
      readiness: null,
      destinations: null,
      trustedStatus: null,
    });
  });

  it("shows locked fields disabled with the deployer note and a header count", async () => {
    render(<SettingsModal />);
    const sw = await screen.findByRole("switch", { name: "Review outbound searches" });
    expect(sw).toHaveAttribute("aria-disabled", "true");
    expect(screen.getByText("6 set by the deployer")).toBeInTheDocument();
    expect(screen.getAllByText("Set by the deployer").length).toBeGreaterThanOrEqual(4);
    expect(screen.getByRole("button", { name: "Don't send" })).toBeDisabled();
    await userEvent.click(sw);
    expect(patches).toHaveLength(0);
  });

  it("disables every control of a locked advanced field and shows the note", async () => {
    render(<SettingsModal />);
    await userEvent.click(await screen.findByRole("button", { name: "Review model and timeout" }));
    const input = await screen.findByLabelText("Review model");
    expect(input).toBeDisabled();
    const row = input.closest("div.mb-4") as HTMLElement;
    expect(within(row).getByRole("button", { name: "Save" })).toBeDisabled();
    expect(within(row).getByText("Set by the deployer")).toBeInTheDocument();
  });

  it("renders a locked bool in the generic row disabled with the note", async () => {
    render(<SettingsModal />);
    await userEvent.click(await screen.findByRole("button", { name: "Review model and timeout" }));
    const sw = await screen.findByRole("switch", { name: /Extra outbound flag/ });
    expect(sw).toHaveAttribute("aria-disabled", "true");
    const row = sw.closest("div.mb-4") as HTMLElement;
    expect(within(row).getByText("Set by the deployer")).toBeInTheDocument();
    await userEvent.click(sw);
    expect(patches).toHaveLength(0);
  });

  it("shows the cloud-model notice as one line inside the scrolling body", async () => {
    render(<SettingsModal />);
    const line = await screen.findByText(
      "1 setting uses a cloud model, so some text may leave this device.",
    );
    // Inside the pane's scrolling body, above the first settings group.
    expect(line.closest(".overflow-y-auto")).not.toBeNull();
    const toggle = screen.getByRole("button", { name: /Show which/ });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    const list = screen.getByTestId("cloud-model-list");
    expect(list.closest("[inert]")).not.toBeNull();
    await userEvent.click(toggle);
    expect(screen.getByRole("button", { name: /Hide/ })).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("cloud-model-list").closest("[inert]")).toBeNull();
    // The en catalog label ("Small-tier model"), not the backend fixture text.
    expect(within(list).getByText("gemini/gemini-3.5-flash")).toBeInTheDocument();
    expect(within(list).getByText("1 setting: Small-tier model")).toBeInTheDocument();
  });

  it("groups the cloud-model list by model and names server-environment keys", async () => {
    nonLocal = [
      { key: "PAPERHUB_MODEL_SMALL", model: "gemini/a" },
      { key: "PAPERHUB_MARKER_LLM_MODEL", model: "gemini/a" },
      { key: "PAPERHUB_AGENT_VISION", model: "gemini/b" },
      { key: "PAPERHUB_SOME_NEW_MODEL", model: "gemini/b" },
    ];
    render(<SettingsModal />);
    await screen.findByText(
      "4 settings use a cloud model, so some text may leave this device.",
    );
    const list = screen.getByTestId("cloud-model-list");
    const terms = within(list).getAllByRole("term");
    expect(terms.map((x) => x.textContent)).toEqual(["gemini/a", "gemini/b"]);
    const defs = within(list).getAllByRole("definition");
    expect(defs[0]).toHaveTextContent(
      "2 settings: Small-tier model, Marker accuracy pass (server environment)",
    );
    expect(defs[1]).toHaveTextContent(
      "2 settings: Agent vision (server environment), Some new model (server environment)",
    );
    expect(list).not.toHaveTextContent("PAPERHUB_");
  });

  it("lists the allowed destinations", async () => {
    render(<SettingsModal />);
    expect(await screen.findByText("export.arxiv.org")).toBeInTheDocument();
    expect(screen.getByText("search.lab.internal")).toBeInTheDocument();
  });

  it("adds a sensitive term with one PATCH of the joined list", async () => {
    render(<SettingsModal />);
    const input = await screen.findByLabelText("Sensitive terms");
    await userEvent.type(input, "Orion{Enter}");
    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0]).toEqual({ PAPERHUB_SENSITIVE_TERMS: "Atlas, Orion" });
  });

  it("rolls a sensitive term back and toasts when the save is refused (403)", async () => {
    patchStatus = 403;
    render(<SettingsModal />);
    const input = await screen.findByLabelText("Sensitive terms");
    await userEvent.type(input, "Orion{Enter}");
    await waitFor(() => expect(toastError).toHaveBeenCalledWith("Set by the deployer"));
    await waitFor(() => expect(screen.queryByText("Orion")).not.toBeInTheDocument());
    expect(screen.getByText("Atlas")).toBeInTheDocument();
  });

  it("public search: switch opens the warning (no PATCH); Turn on saves 1", async () => {
    render(<SettingsModal />);
    const sw = await screen.findByRole("switch", { name: "Public web search" });
    await userEvent.click(sw);
    expect(patches).toHaveLength(0);
    const panel = screen.getByTestId("public-search-warning");
    expect(within(panel).getByText("Your search text will leave this device.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Turn on public search" }));
    await waitFor(() => expect(patches).toEqual([{ PAPERHUB_PUBLIC_WEB_SEARCH: "1" }]));
  });

  it("shows when public search was turned on, from updated_at", async () => {
    fields = privacyFields().map((x) =>
      x.key === "PAPERHUB_PUBLIC_WEB_SEARCH"
        ? { ...x, value: "1", updated_at: "2026-11-13 10:42:00" }
        : x,
    );
    render(<SettingsModal />);
    expect(await screen.findByText(/Turned on by you, .*2026/)).toBeInTheDocument();
  });

  it("the private-upload default switch saves immediately", async () => {
    render(<SettingsModal />);
    await userEvent.click(await screen.findByRole("switch", { name: "New uploads are private" }));
    await waitFor(() => expect(patches).toEqual([{ PAPERHUB_PRIVATE_UPLOAD_DEFAULT: "1" }]));
  });

  it("shows Reachable under the trusted search URL, Not reachable when the probe fails", async () => {
    const { unmount } = render(<SettingsModal />);
    expect(await screen.findByText("Reachable")).toBeInTheDocument();
    unmount();
    reachable = false;
    useSettingsStore.setState({ config: null, trustedStatus: null });
    render(<SettingsModal />);
    expect(await screen.findByText("Not reachable")).toBeInTheDocument();
  });

  it("re-probes after the trusted search URL is saved", async () => {
    render(<SettingsModal />);
    await screen.findByText("Reachable");
    const before = probes;
    const input = screen.getByLabelText("Trusted search service");
    await userEvent.clear(input);
    await userEvent.type(input, "https://new.example{Enter}");
    await waitFor(() =>
      expect(patches).toEqual([{ PAPERHUB_TRUSTED_SEARCH_URL: "https://new.example" }]),
    );
    await waitFor(() => expect(probes).toBeGreaterThan(before));
  });

  it("rejects a non-http(s) trusted search URL without saving", async () => {
    render(<SettingsModal />);
    const input = await screen.findByLabelText("Trusted search service");
    await userEvent.clear(input);
    await userEvent.type(input, "not a url{Enter}");
    expect(
      await screen.findByText("Enter a full http:// or https:// address, or leave it empty."),
    ).toBeInTheDocument();
    expect(patches).toHaveLength(0);
  });

  it("reverts the trusted search URL draft when the save fails", async () => {
    patchStatus = 500;
    render(<SettingsModal />);
    const input = await screen.findByLabelText<HTMLInputElement>("Trusted search service");
    await userEvent.clear(input);
    await userEvent.type(input, "https://new.example{Enter}");
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    await waitFor(() => expect(input.value).toBe("https://s.example"));
  });
});
