import { create } from "zustand";

import {
  createIdeationProblem,
  getIdeationExploreStatus,
  getIdeationGraph,
  getIdeationProblem,
  listIdeationProblems,
  patchIdeationDomain,
  patchIdeationIdea,
  postIdeationExplore,
  putIdeationDocumentDomains,
  retryIdeationProblem,
  undoDismissIdeationIdea,
} from "@/lib/api";
import i18n from "@/lib/i18n";
import { prefersReducedMotion } from "@/lib/motion";
import type {
  DocumentDomainsPut,
  DomainPatch,
  ExploreStatus,
  GraphResponse,
  Idea,
  IdeaPatch,
  IdeaStatus,
  Problem,
} from "@/types/ideation";

export type IdeationScope = "explore" | number;
export type IdeationView = "graph" | "cards";

/** The status filter chips, in display order. */
export const IDEA_FILTERS = ["active", "saved", "trying", "done", "dismissed"] as const;
export type IdeaFilter = (typeof IDEA_FILTERS)[number];

export type IdeationSelection =
  | { kind: "idea"; id: number }
  | { kind: "doc"; id: number }
  | { kind: "domain"; id: number }
  | { kind: "problem" }
  | null;

export const IDEATION_POLL_MS = 2000;
/** How long a dismissed idea keeps drawing while it fades out of the graph. */
export const IDEA_FADE_MS = 200;

const SAVED_KEY = "paperhub-ideas-state";

/** The reader's last scope and view, remembered across reloads (best effort: storage may be blocked). */
function readSaved(): { scope: IdeationScope; view: IdeationView } {
  const out: { scope: IdeationScope; view: IdeationView } = { scope: "explore", view: "graph" };
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(SAVED_KEY) ?? "null");
    if (raw && typeof raw === "object") {
      const { scope, view } = raw as { scope?: unknown; view?: unknown };
      if (typeof scope === "number" && Number.isInteger(scope)) out.scope = scope;
      if (view === "cards" || view === "graph") out.view = view;
    }
  } catch {
    /* unreadable or blocked: start from the defaults */
  }
  return out;
}

function writeSaved(scope: IdeationScope, view: IdeationView): void {
  try {
    localStorage.setItem(SAVED_KEY, JSON.stringify({ scope, view }));
  } catch {
    /* blocked: the choice just won't survive a reload */
  }
}

/** Whether an idea with this status shows under a filter chip. */
export function matchesFilter(status: IdeaStatus, filter: IdeaFilter): boolean {
  switch (filter) {
    case "active":
      return status !== "dismissed";
    case "done":
      return status === "worked" || status === "failed";
    default:
      return status === filter;
  }
}

/** The count shown on each filter chip. */
export function filterCounts(ideas: Idea[]): Record<IdeaFilter, number> {
  const out = { active: 0, saved: 0, trying: 0, done: 0, dismissed: 0 };
  for (const idea of ideas) {
    for (const f of IDEA_FILTERS) if (matchesFilter(idea.status, f)) out[f] += 1;
  }
  return out;
}

interface IdeationState {
  problems: Problem[];
  scope: IdeationScope;
  view: IdeationView;
  filter: IdeaFilter;
  selected: IdeationSelection;
  graph: GraphResponse | null;
  /** Non-dismissed free-exploration ideas, known once that graph has loaded. */
  exploreIdeaCount: number | null;
  /** Library-wide document counts; kept across scope switches so the header doesn't blink. */
  libraryDocs: number | null;
  libraryPrivate: number | null;
  loading: boolean;
  /** The current scope's graph load or problem poll failed. */
  error: string | null;
  /** The problem list failed to load. */
  listError: string | null;
  submitting: boolean;
  /** Load the problem list and the current scope's graph. */
  load: () => Promise<void>;
  /** Refetch only the problem list. */
  loadProblems: () => Promise<void>;
  setScope: (scope: IdeationScope) => void;
  setView: (view: IdeationView) => void;
  setFilter: (filter: IdeaFilter) => void;
  setSelected: (selected: IdeationSelection) => void;
  /** Create a problem, switch to it and poll it until it stops generating. */
  submitProblem: (text: string, tried: string) => Promise<void>;
  /** Re-run a failed problem and poll it again (rejects when the request fails). */
  retryProblem: (problemId: number) => Promise<void>;
  patchIdea: (ideaId: number, patch: IdeaPatch) => Promise<Idea>;
  /** Ideas fading out after a dismiss; the graph keeps drawing them at opacity 0. */
  leavingIdeaIds: number[];
  /** Dismiss with reasons: the idea fades out and the drawer closes unless the dismissed filter shows it. */
  dismissIdea: (ideaId: number, reasons: string[], note: string) => Promise<Idea>;
  /** Undo a dismiss: the backend restores the exact previous status.
   *  A second Undo (in flight, or already undone) is a no-op that resolves null. */
  undoDismiss: (ideaId: number) => Promise<Idea | null>;
  /** Ideas whose undo-dismiss request is in flight. */
  undoingIdeaIds: number[];
  /** Record a chat session opened from an idea (its chat count). */
  addIdeaSession: (ideaId: number, sessionId: number) => void;
  /** Manually set a document's domains, then refetch the graph (colours may change). */
  setDocumentDomains: (docId: number, body: DocumentDomainsPut) => Promise<void>;
  /** Rename or merge a domain, then refetch the graph. */
  patchDomain: (domainId: number, body: DomainPatch) => Promise<void>;
  /** A free-exploration run is in flight (started here, or found running on load). */
  exploring: boolean;
  /** The latest explore status read (card progress, skipped documents). */
  exploreStatus: ExploreStatus | null;
  /**
   * Start a free-exploration run, poll its status until it stops running,
   * then reload the explore graph (when it's still the scope). Resolves to the
   * final status (`last_new_count` 0 = nothing new, Ruling U3), or null when
   * the poll was cancelled (page unmount). Rejects when a request fails.
   */
  explore: () => Promise<ExploreStatus | null>;
  /** Cancel any problem or explore poll (page unmount). */
  stopPolling: () => void;
}

// A poll belongs to one token; bumping it cancels the poll in flight.
let pollToken = 0;
let pollTimer: ReturnType<typeof setTimeout> | null = null;
// The explore poll has its own token and timer (it runs beside a problem poll).
let exploreToken = 0;
let exploreTimer: ReturnType<typeof setTimeout> | null = null;
// Wakes a cancelled poll's pending wait so explore() resolves (null) instead of hanging.
let exploreWake: (() => void) | null = null;
// Same idea for graph loads: only the latest request may land.
let graphSeq = 0;

function cancelPoll(): void {
  pollToken += 1;
  if (pollTimer !== null) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function cancelExplorePoll(): void {
  exploreToken += 1;
  if (exploreTimer !== null) {
    clearTimeout(exploreTimer);
    exploreTimer = null;
  }
  const wake = exploreWake;
  exploreWake = null;
  wake?.();
}

const errText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Ideas page state. Not persisted: the backend is the source of truth. */
export const useIdeationStore = create<IdeationState>()((set, get) => {
  const upsertProblem = (p: Problem) =>
    set((s) => {
      const i = s.problems.findIndex((x) => x.id === p.id);
      if (i < 0) return { problems: [p, ...s.problems] };
      const problems = s.problems.slice();
      problems[i] = p;
      return { problems };
    });

  // Swap in the server's copy of an idea and keep the scope's idea count
  // (which excludes dismissed ideas, ruling R3) in step.
  const applyIdea = (idea: Idea) =>
    set((s) => {
      if (!s.graph) return {};
      const before = s.graph.ideas.find((i) => i.id === idea.id);
      const ideas = s.graph.ideas.map((i) => (i.id === idea.id ? idea : i));
      const out: Partial<IdeationState> = { graph: { ...s.graph, ideas } };
      if (s.scope === "explore") {
        out.exploreIdeaCount = ideas.filter((i) => i.status !== "dismissed").length;
      } else if (before) {
        const delta = Number(before.status === "dismissed") - Number(idea.status === "dismissed");
        if (delta !== 0) {
          out.problems = s.problems.map((p) =>
            p.id === s.scope ? { ...p, idea_count: Math.max(0, p.idea_count + delta) } : p,
          );
        }
      }
      return out;
    });

  const loadGraph = async (scope: IdeationScope) => {
    const seq = ++graphSeq;
    set({ loading: true, error: null });
    try {
      const graph = await getIdeationGraph(scope);
      if (seq !== graphSeq || get().scope !== scope) return;
      set({
        graph,
        loading: false,
        // The whole library, not the scope's documents.
        libraryDocs: graph.library_count,
        libraryPrivate: graph.private_count,
        ...(scope === "explore"
          ? { exploreIdeaCount: graph.ideas.filter((i) => i.status !== "dismissed").length }
          : {}),
      });
      if (graph.problem) upsertProblem(graph.problem);
    } catch (err) {
      if (seq !== graphSeq) return;
      set({ loading: false, error: errText(err) });
    }
  };

  const poll = (problemId: number) => {
    cancelPoll();
    const token = pollToken;
    const tick = async () => {
      pollTimer = null;
      let p: Problem;
      try {
        p = await getIdeationProblem(problemId);
      } catch (err) {
        if (token === pollToken) set({ error: errText(err) });
        return; // stop on error
      }
      if (token !== pollToken) return;
      upsertProblem(p);
      if (p.status === "generating") {
        pollTimer = setTimeout(() => void tick(), IDEATION_POLL_MS);
      } else if (get().scope === problemId) {
        await loadGraph(problemId);
      }
    };
    pollTimer = setTimeout(() => void tick(), IDEATION_POLL_MS);
  };

  // Poll the explore status until the run stops, then reload the explore graph.
  const watchExplore = async (token: number): Promise<ExploreStatus | null> => {
    const done = (status: ExploreStatus | null) => {
      if (token === exploreToken) set({ exploring: false });
      return status;
    };
    let status: ExploreStatus;
    for (;;) {
      await new Promise<void>((resolve) => {
        exploreWake = resolve;
        exploreTimer = setTimeout(() => {
          exploreTimer = null;
          exploreWake = null;
          resolve();
        }, IDEATION_POLL_MS);
      });
      if (token !== exploreToken) return null;
      try {
        status = await getIdeationExploreStatus();
      } catch (err) {
        done(null);
        throw err;
      }
      if (token !== exploreToken) return null;
      set({ exploreStatus: status });
      if (!status.running) break;
    }
    if (get().scope === "explore") await loadGraph("explore");
    if (token !== exploreToken) return null;
    return done(status);
  };

  // On page load: an explore run already in flight (started before a reload
  // or from another tab) resumes its poll; a finished one shows its skipped count.
  const resumeExplore = async () => {
    const token = exploreToken;
    let status: ExploreStatus;
    try {
      status = await getIdeationExploreStatus();
    } catch {
      return; // best effort: the page works without it
    }
    if (token !== exploreToken) return;
    set({ exploreStatus: status });
    if (!status.running || get().exploring) return;
    set({ exploring: true });
    void watchExplore(token).catch(() => undefined);
  };

  const pollIfGenerating = (scope: IdeationScope) => {
    if (scope === "explore") return;
    const p = get().problems.find((x) => x.id === scope);
    if (p?.status === "generating") poll(scope);
  };

  const saved = readSaved();
  return {
    problems: [],
    scope: saved.scope,
    view: saved.view,
    filter: "active",
    selected: null,
    graph: null,
    exploreIdeaCount: null,
    libraryDocs: null,
    libraryPrivate: null,
    loading: false,
    error: null,
    listError: null,
    submitting: false,

    load: async () => {
      // A stopPolling() (page unmount) while this load is in flight must not
      // be undone by the poll it would start when it lands.
      const token = pollToken;
      void resumeExplore();
      await get().loadProblems();
      // A remembered problem that no longer exists (deleted, or another library) falls back to free exploration.
      const s0 = get();
      if (typeof s0.scope === "number" && !s0.listError && !s0.problems.some((p) => p.id === s0.scope)) {
        set({ scope: "explore", selected: null });
        writeSaved("explore", s0.view);
      }
      const { scope } = get();
      await loadGraph(scope);
      if (token === pollToken && scope === get().scope) pollIfGenerating(scope);
    },

    loadProblems: async () => {
      try {
        const problems = await listIdeationProblems();
        set({ problems, listError: null });
      } catch (err) {
        set({ listError: errText(err) });
      }
    },

    setScope: (scope) => {
      if (scope === get().scope) return;
      cancelPoll();
      set({ scope, selected: null, graph: null });
      writeSaved(scope, get().view);
      void loadGraph(scope);
      pollIfGenerating(scope);
    },

    setView: (view) => {
      set({ view });
      writeSaved(get().scope, view);
    },
    setFilter: (filter) => set({ filter }),
    setSelected: (selected) => set({ selected }),

    submitProblem: async (text, tried) => {
      set({ submitting: true });
      let p: Problem;
      try {
        p = await createIdeationProblem({ text, tried });
      } catch (err) {
        // The caller toasts it; the current scope's graph is still fine.
        set({ submitting: false });
        throw err;
      }
      upsertProblem(p);
      set({ submitting: false });
      get().setScope(p.id);
    },

    retryProblem: async (problemId) => {
      const p = await retryIdeationProblem(problemId);
      upsertProblem(p);
      if (get().scope === problemId) poll(problemId);
    },

    patchIdea: async (ideaId, patch) => {
      const idea = await patchIdeationIdea(ideaId, patch);
      applyIdea(idea);
      return idea;
    },

    leavingIdeaIds: [],

    dismissIdea: async (ideaId, reasons, note) => {
      set((s) => ({ leavingIdeaIds: [...s.leavingIdeaIds, ideaId] }));
      const unleave = () =>
        set((s) => ({ leavingIdeaIds: s.leavingIdeaIds.filter((id) => id !== ideaId) }));
      let idea: Idea;
      try {
        idea = await patchIdeationIdea(ideaId, {
          status: "dismissed",
          dismiss_reasons: reasons,
          dismiss_note: note,
        });
      } catch (err) {
        unleave();
        throw err;
      }
      applyIdea(idea);
      const { selected, filter } = get();
      if (filter !== "dismissed" && selected?.kind === "idea" && selected.id === ideaId) {
        set({ selected: null });
      }
      setTimeout(unleave, prefersReducedMotion() ? 0 : IDEA_FADE_MS);
      return idea;
    },

    undoingIdeaIds: [],

    undoDismiss: async (ideaId) => {
      const s0 = get();
      const current = s0.graph?.ideas.find((i) => i.id === ideaId);
      if (s0.undoingIdeaIds.includes(ideaId) || (current && current.status !== "dismissed")) return null;
      set((s) => ({ undoingIdeaIds: [...s.undoingIdeaIds, ideaId] }));
      let idea: Idea;
      try {
        idea = await undoDismissIdeationIdea(ideaId);
      } finally {
        set((s) => ({ undoingIdeaIds: s.undoingIdeaIds.filter((id) => id !== ideaId) }));
      }
      applyIdea(idea);
      // Bring it back into view, unless the user has since moved to another scope.
      if (get().graph?.ideas.some((i) => i.id === ideaId)) {
        if (!matchesFilter(idea.status, get().filter)) set({ filter: "active" });
        set({ selected: { kind: "idea", id: ideaId } });
      }
      return idea;
    },

    addIdeaSession: (ideaId, sessionId) =>
      set((s) => {
        if (!s.graph) return {};
        const ideas = s.graph.ideas.map((i) =>
          i.id === ideaId && !i.session_ids.includes(sessionId)
            ? { ...i, session_ids: [...i.session_ids, sessionId] }
            : i,
        );
        return { graph: { ...s.graph, ideas } };
      }),

    setDocumentDomains: async (docId, body) => {
      const doc = await putIdeationDocumentDomains(docId, body);
      set((s) => {
        if (!s.graph) return {};
        const documents = s.graph.documents.map((d) =>
          d.id === docId ? { ...d, domains: doc.domains } : d,
        );
        return { graph: { ...s.graph, documents } };
      });
      // New domains and colour indices come from the server.
      await loadGraph(get().scope);
    },

    patchDomain: async (domainId, body) => {
      const domains = await patchIdeationDomain(domainId, body);
      // The response lists every library domain: keep only the scope's (a
      // merge target outside the scope arrives with the reload below).
      set((s) => {
        if (!s.graph) return {};
        const inScope = new Set(s.graph.domains.map((d) => d.id));
        return { graph: { ...s.graph, domains: domains.filter((d) => inScope.has(d.id)) } };
      });
      // A merge moves documents to the target domain.
      await loadGraph(get().scope);
    },

    exploring: false,
    exploreStatus: null,

    explore: async () => {
      cancelExplorePoll();
      const token = exploreToken;
      set({ exploring: true, exploreStatus: null });
      try {
        await postIdeationExplore(i18n.language);
      } catch (err) {
        if (token === exploreToken) set({ exploring: false });
        throw err;
      }
      if (token !== exploreToken) return null;
      return watchExplore(token);
    },

    stopPolling: () => {
      cancelPoll();
      cancelExplorePoll();
      set({ exploring: false });
    },
  };
});
