import {
  BarChart3,
  BookMarked,
  ChevronDown,
  ChevronsLeft,
  ChevronsRight,
  Clock,
  CornerDownRight,
  Lightbulb,
  MessageSquare,
  Plus,
  ShieldQuestion,
  Trash2,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { AccountMenu } from "@/components/layout/AccountMenu";
import { ThemeToggle } from "@/components/layout/ThemeToggle";
import { ReferenceSourcesPanel } from "@/components/references/ReferenceSourcesPanel";
import { usePresence } from "@/hooks/usePresence";
import { deleteBackendSession, getSessionFeedbackCount, restoreBackendSession } from "@/lib/api";
import { prefersReducedMotion, revealInScroll } from "@/lib/motion";
import { cn } from "@/lib/utils";
import { groupSessionsByTime, type TimeSection } from "@/lib/sessionTree";
import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import { useSettingsStore } from "@/store/settings";

/** Feedback count chip: grows in, shrinks out (reduced motion: instant). */
const BADGE_MOTION =
  "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=closed]:zoom-out-95 motion-reduce:animate-none";

/** localStorage key: the time sections of the chat list the user collapsed. */
const COLLAPSED_GROUPS_KEY = "paperhub-sidebar-collapsed-groups";

function readCollapsedGroups(): Set<string> {
  try {
    const raw = localStorage.getItem(COLLAPSED_GROUPS_KEY);
    const list: unknown = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(list) ? list.filter((x) => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

/** Held-searches entry: collapses in / out so the rows below glide (reduced motion: instant). */
const HELD_COLLAPSE =
  "grid grid-rows-[1fr] opacity-100 transition-[grid-template-rows,opacity] duration-200 ease-out data-[state=closed]:grid-rows-[0fr] data-[state=closed]:opacity-0 motion-reduce:transition-none";

/** Amber pill on a chat row with cards waiting for the user's OK (held
 *  searches, and Ask-first actions of a paused run).
 *  Fades/zooms in and out; keeps its last count while leaving. */
function HeldMark({ count }: { count: number }) {
  const { t } = useTranslation("outbound");
  const presence = usePresence(count > 0, 200);
  const [shown, setShown] = useState(count);
  if (count > 0 && count !== shown) setShown(count);
  if (!presence.mounted) return null;
  const label = t("held.chatMark", "Paused: {{count}} items need your OK", { count: shown });
  return (
    <span
      role="img"
      aria-label={label}
      title={label}
      data-state={presence.state}
      className={cn(
        "inline-flex shrink-0 items-center gap-0.5 rounded-full border border-amber-300 bg-amber-50 px-1.5 text-[11px] tabular-nums text-amber-800 duration-200 dark:border-amber-800 dark:bg-amber-950/60 dark:text-amber-200",
        BADGE_MOTION,
      )}
    >
      <ShieldQuestion className="h-3 w-3" aria-hidden />
      {shown}
    </span>
  );
}

export function Sidebar() {
  const { t, i18n } = useTranslation("states");
  const { t: tOut } = useTranslation("outbound");
  const openSettings = useSettingsStore((s) => s.open);
  const openSettingsAt = useSettingsStore((s) => s.openAt);
  const heldBadge = useNavStore((s) => s.heldBadge);
  const heldBySession = useNavStore((s) => s.heldBySession);
  const pausedActionsBySession = useNavStore((s) => s.pausedActionsBySession);
  // The entry collapses in / out with the count; keep the last count meanwhile.
  const heldPresence = usePresence(heldBadge > 0, 200);
  const [shownHeld, setShownHeld] = useState(heldBadge);
  if (heldBadge > 0 && heldBadge !== shownHeld) setShownHeld(heldBadge);
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);

  // Chats grouped by last activity, newest first (Today, Yesterday, ...).
  const timeSections = groupSessionsByTime(sessions, new Date());
  const [collapsedGroups, setCollapsedGroups] = useState(readCollapsedGroups);
  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSED_GROUPS_KEY, JSON.stringify([...collapsedGroups]));
    } catch {
      // Private mode / blocked storage: the sections just reopen next time.
    }
  }, [collapsedGroups]);
  const toggleGroup = (key: string) => {
    const opening = collapsedGroups.has(key);
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (!next.delete(key)) next.add(key);
      return next;
    });
    // Bring a section opened near the bottom into view once it has grown.
    if (opening) {
      setTimeout(
        () => {
          const el = document.getElementById(`chat-group-${key}`);
          if (el) revealInScroll(el);
        },
        prefersReducedMotion() ? 0 : 200,
      );
    }
  };
  // The active chat's section opens whenever the active chat moves into it.
  const activeGroup =
    timeSections.find((sec) => sec.rows.some((r) => r.session.id === activeSessionId))?.key ??
    null;
  const [seenActiveGroup, setSeenActiveGroup] = useState<string | null>(null);
  if (activeGroup !== seenActiveGroup) {
    setSeenActiveGroup(activeGroup);
    if (activeGroup !== null && collapsedGroups.has(activeGroup)) {
      const next = new Set(collapsedGroups);
      next.delete(activeGroup);
      setCollapsedGroups(next);
    }
  }
  const sectionLabel = (sec: TimeSection): string => {
    switch (sec.bucket) {
      case "today":
        return t("sidebar.group.today", "Today");
      case "yesterday":
        return t("sidebar.group.yesterday", "Yesterday");
      case "d7":
        return t("sidebar.group.d7", "Previous 7 days");
      case "d30":
        return t("sidebar.group.d30", "Previous 30 days");
      case "month":
        return new Intl.DateTimeFormat(i18n.language, { month: "long", year: "numeric" }).format(
          sec.month,
        );
      default:
        return t("sidebar.group.older", "Older");
    }
  };
  const newSession = useChatStore((s) => s.newSession);
  const selectSession = useChatStore((s) => s.selectSession);
  const collapsed = useChatStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useChatStore((s) => s.toggleSidebar);
  const sidebarTab = useChatStore((s) => s.sidebarTab);
  const setSidebarTab = useChatStore((s) => s.setSidebarTab);

  const page = useNavStore((s) => s.page);
  const setPage = useNavStore((s) => s.setPage);
  const feedbackBadge = useNavStore((s) => s.feedbackBadge);
  // The chip animates out when the count drops to 0; keep showing the last
  // non-zero count meanwhile so it doesn't flash "0" on its way out.
  const badgePresence = usePresence(feedbackBadge > 0);
  const [shownBadge, setShownBadge] = useState(feedbackBadge);
  if (feedbackBadge > 0 && feedbackBadge !== shownBadge) setShownBadge(feedbackBadge);

  // Count references for the active session to drive the tab badge.
  const referencesBySession = useChatStore((s) => s.referencesBySession);
  const activeBackendId =
    activeSessionId !== null
      ? (sessions.find((s) => s.id === activeSessionId)?.backend_session_id ?? null)
      : null;
  const refsCount =
    activeBackendId !== null
      ? (referencesBySession[activeBackendId]?.length ?? 0)
      : 0;

  const handleDelete = (e: React.MouseEvent, sessionId: number) => {
    e.stopPropagation();
    const currentSessions = useChatStore.getState().sessions;
    const idx = currentSessions.findIndex((s) => s.id === sessionId);
    const removed = useChatStore.getState().deleteSession(sessionId);
    if (!removed) return;

    const backendSessionId = removed.backend_session_id;
    // Deleting a chat takes its feedback (and the training data built from it)
    // out of the analysis and exports: say so when there is any.
    const feedbackCount: Promise<number> =
      backendSessionId != null
        ? getSessionFeedbackCount(backendSessionId).catch(() => 0)
        : Promise.resolve(0);

    // Delete authoritatively + immediately so the chat is gone on every
    // device right away (empty sessions are hard-deleted server-side; ones
    // with content are tombstoned). A local-only draft has no backend row.
    if (backendSessionId != null) {
      void deleteBackendSession(backendSessionId).catch((err: unknown) => {
        // Roll the local removal back so the UI matches the server.
        useChatStore.getState().restoreSession(removed, idx);
        toast.error(t("sessionToast.deleteFailed"), {
          description: err instanceof Error ? err.message : String(err),
        });
      });
    }

    void feedbackCount.then((nFeedback) => toast(t("sessionToast.deleted"), {
      description:
        nFeedback > 0 ? (
          <span>
            {removed.title}
            <br />
            {t("sessionToast.feedbackNote", { count: nFeedback })}
          </span>
        ) : (
          removed.title
        ),
      action: {
        label: t("sessionToast.undo"),
        onClick: () => {
          useChatStore.getState().restoreSession(removed, idx);
          // Un-tombstone on the server. An empty session was hard-deleted, so
          // restore 404s — that's expected: the local copy is back and the
          // backend row re-materialises on the next message. Only surface a
          // genuine (non-404) failure.
          if (backendSessionId != null) {
            void restoreBackendSession(backendSessionId).catch((err: unknown) => {
              const msg = err instanceof Error ? err.message : String(err);
              if (!msg.includes("404")) {
                toast.error(t("sessionToast.restoreFailed"));
              }
            });
          }
        },
      },
      duration: nFeedback > 0 ? 9000 : 5000,
    }));
  };

  const isMac =
    typeof navigator !== "undefined" &&
    navigator.userAgent.toLowerCase().includes("mac");
  const kbdNew = isMac ? "⌘K" : "Ctrl+K";

  const badgeText = t("nav.feedbackBadge", "{{count}} need your confirmation", {
    count: feedbackBadge,
  });

  const pageEntries = [
    { id: "ideas", Icon: Lightbulb, label: t("nav.ideas", "Ideas") },
    { id: "feedback", Icon: BarChart3, label: t("nav.feedback", "Feedback") },
  ] as const;

  const heldLabel = tOut("nav.held", "Held searches");
  const heldOpen = heldBadge > 0;
  const heldCountText = tOut("nav.heldBadge", "{{count}} held searches waiting", {
    count: shownHeld,
  });
  const openHeld = () => openSettingsAt("outbound_log", "held");

  // Each tab gets its own click-to-expand icon when collapsed.
  const expandToTab = (tab: "chats" | "references") => {
    setSidebarTab(tab);
    if (collapsed) toggleSidebar();
  };

  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <div className="shrink-0 h-(--header-h) flex items-center justify-between px-4 border-b border-border">
        {!collapsed && (
          <span className="text-lg font-semibold">{t("appName")}</span>
        )}
        <div className="flex items-center gap-1 ml-auto">
          {!collapsed && <ThemeToggle />}
          <Button
            variant="ghost"
            size="icon"
            aria-label={collapsed ? t("sidebar.expand") : t("sidebar.collapse")}
            onClick={toggleSidebar}
          >
            {collapsed ? (
              <ChevronsRight className="h-4 w-4" />
            ) : (
              <ChevronsLeft className="h-4 w-4" />
            )}
          </Button>
        </div>
      </div>

      {collapsed ? (
        /* Collapsed: vertical icon rail.  Each icon both selects the tab AND
           expands the sidebar so the user always lands in the right view. */
        <div className="p-2 flex flex-col gap-1">
          <TooltipProvider>
            {pageEntries.map(({ id, Icon, label }) => (
              <Tooltip key={id}>
                <TooltipTrigger
                  render={
                    <Button
                      size="icon"
                      variant="ghost"
                      onClick={() => setPage(id)}
                      // aria-label overrides content, so the pending count
                      // must be part of it to be announced.
                      aria-label={
                        id === "feedback" && feedbackBadge > 0
                          ? `${label}, ${badgeText}`
                          : label
                      }
                      aria-current={page === id ? "page" : undefined}
                      className={cn("relative w-full", page === id && "bg-accent")}
                    />
                  }
                >
                  <Icon className="h-4 w-4" />
                  {id === "feedback" && badgePresence.mounted && (
                    <span
                      aria-hidden="true"
                      data-state={badgePresence.state}
                      className={cn(
                        "absolute -right-0.5 -top-0.5 inline-flex items-center justify-center min-w-4 h-4 rounded-full bg-muted px-1 text-[10px] text-foreground tabular-nums duration-150",
                        BADGE_MOTION,
                      )}
                    >
                      {shownBadge}
                    </span>
                  )}
                </TooltipTrigger>
                <TooltipContent side="right">{label}</TooltipContent>
              </Tooltip>
            ))}
          </TooltipProvider>
          {heldPresence.mounted && (
            <div data-state={heldPresence.state} inert={!heldOpen} className={HELD_COLLAPSE}>
              <div className="min-h-0 overflow-hidden">
                <Button
                  size="icon"
                  variant="ghost"
                  onClick={openHeld}
                  aria-label={`${heldLabel}, ${heldCountText}`}
                  className="relative w-full"
                >
                  <Clock className="h-4 w-4" />
                  <span
                    aria-hidden="true"
                    className="absolute -right-0.5 -top-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-foreground px-1 text-[10px] tabular-nums text-background"
                  >
                    {shownHeld}
                  </span>
                </Button>
              </div>
            </div>
          )}
          <div className="my-1 h-px bg-border" role="separator" />
          <Button
            size="icon"
            variant={sidebarTab === "chats" ? "default" : "ghost"}
            onClick={() => expandToTab("chats")}
            aria-label={t("sidebar.tabChats")}
            aria-current={sidebarTab === "chats" ? "page" : undefined}
            className="w-full"
          >
            <MessageSquare className="h-4 w-4" />
          </Button>
          <Button
            size="icon"
            variant={sidebarTab === "references" ? "default" : "ghost"}
            onClick={() => expandToTab("references")}
            aria-label={
              refsCount > 0
                ? t("sidebar.referencesWithCount", { count: refsCount })
                : t("sidebar.tabReferences")
            }
            aria-current={sidebarTab === "references" ? "page" : undefined}
            className="w-full"
          >
            <BookMarked className="h-4 w-4" />
          </Button>
        </div>
      ) : (
        <>
          {/* Page entries — Ideas / Feedback sit above the chat/references tabs. */}
          <div className="flex flex-col gap-0.5 p-2 border-b border-border">
            {pageEntries.map(({ id, Icon, label }) => (
              <button
                key={id}
                type="button"
                onClick={() => setPage(id)}
                aria-current={page === id ? "page" : undefined}
                className={cn(
                  "w-full inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm transition-colors",
                  page === id
                    ? "bg-accent text-accent-foreground"
                    : "text-foreground hover:bg-accent/50",
                )}
              >
                <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                {label}
                {id === "feedback" && badgePresence.mounted && (
                  <span
                    aria-hidden="true"
                    data-state={badgePresence.state}
                    className={cn(
                      "ml-auto inline-flex items-center justify-center min-w-[1.25rem] h-4 rounded-full bg-muted px-1 text-[10px] tabular-nums duration-150",
                      BADGE_MOTION,
                    )}
                  >
                    {shownBadge}
                  </span>
                )}
                {id === "feedback" && feedbackBadge > 0 && (
                  <>
                    {" "}
                    <span className="sr-only">{badgeText}</span>
                  </>
                )}
              </button>
            ))}
            {heldPresence.mounted && (
              <div data-state={heldPresence.state} inert={!heldOpen} className={HELD_COLLAPSE}>
                <div className="min-h-0 overflow-hidden">
                  <button
                    type="button"
                    onClick={openHeld}
                    className="inline-flex w-full items-center gap-2 rounded-md px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-accent/50"
                  >
                    <Clock className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                    {heldLabel}
                    <span
                      aria-hidden="true"
                      className="ml-auto inline-flex h-4 min-w-[1.25rem] items-center justify-center rounded-full bg-foreground px-1 text-[10px] tabular-nums text-background"
                    >
                      {shownHeld}
                    </span>
                    <span className="sr-only">{heldCountText}</span>
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* Tab bar — single source of truth for which view is active. */}
          <div
            role="tablist"
            aria-label={t("sidebar.sections")}
            className="flex border-b border-border"
          >
            <button
              type="button"
              role="tab"
              aria-selected={sidebarTab === "chats"}
              onClick={() => setSidebarTab("chats")}
              className={`flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                sidebarTab === "chats"
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <MessageSquare className="h-3.5 w-3.5" />
              {t("sidebar.tabChats")}
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={sidebarTab === "references"}
              onClick={() => setSidebarTab("references")}
              className={`flex-1 inline-flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                sidebarTab === "references"
                  ? "border-primary text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
              }`}
            >
              <BookMarked className="h-3.5 w-3.5" />
              {t("sidebar.tabReferences")}
              {refsCount > 0 && (
                <span className="ml-0.5 inline-flex items-center justify-center min-w-[1.25rem] h-4 rounded-full bg-muted px-1 text-[10px] tabular-nums">
                  {refsCount}
                </span>
              )}
            </button>
          </div>

          {/* Tab content */}
          {sidebarTab === "chats" ? (
            <>
              <div className="p-3">
                <Button
                  variant="secondary"
                  className="w-full justify-start gap-2"
                  onClick={() => newSession()}
                >
                  <Plus className="h-4 w-4" /> {t("sidebar.newChat")}
                  <kbd className="ml-auto text-[10px] text-muted-foreground border rounded px-1 py-0.5">
                    {kbdNew}
                  </kbd>
                </Button>
              </div>
              <nav className="flex-1 overflow-y-auto px-2 pb-4">
                {sessions.length === 0 && (
                  <p className="px-2 text-sm text-muted-foreground">
                    {t("sidebar.noChats")}
                  </p>
                )}
                {sessions.length > 0 && (
                  <div className="space-y-1">
                    {timeSections.map((sec) => {
                      const open = !collapsedGroups.has(sec.key);
                      const bodyId = `chat-group-${sec.key}`;
                      return (
                        <section key={sec.key}>
                          <button
                            type="button"
                            aria-expanded={open}
                            aria-controls={bodyId}
                            onClick={() => toggleGroup(sec.key)}
                            className="flex w-full items-center gap-1 rounded px-2 pb-0.5 pt-2 text-left text-[11.5px] text-muted-foreground transition-colors hover:text-foreground"
                          >
                            <span>{sectionLabel(sec)}</span>
                            <ChevronDown
                              aria-hidden
                              className={cn(
                                "h-3.5 w-3.5 transition-transform duration-200 ease-out motion-reduce:transition-none",
                                !open && "-rotate-90",
                              )}
                            />
                          </button>
                          {/* Collapses toward its header so the sections below glide. */}
                          <div
                            id={bodyId}
                            data-state={open ? "open" : "closed"}
                            inert={!open}
                            className={HELD_COLLAPSE}
                          >
                            <div className="min-h-0 overflow-hidden">
                              <ul className="space-y-1 pt-0.5">
                                {sec.rows.map(({ session: s, isFork }) => {
                              const isActive = s.id === activeSessionId;
                              const heldCount =
                                s.backend_session_id !== null
                                  ? (heldBySession[s.backend_session_id] ?? 0) +
                                    (pausedActionsBySession[s.backend_session_id] ?? 0)
                                  : 0;
                              // The indent is visual; give screen readers the lineage
                              // explicitly. Resolve the parent's title from its backend id.
                              const parentTitle = isFork
                                ? sessions.find(
                                    (p) =>
                                      p.backend_session_id === s.forked_from_session_id,
                                  )?.title
                                : undefined;
                              return (
                                <li
                                  key={s.id}
                                  className={`group/row relative ${isFork ? "ml-3" : ""}`}
                                >
                                  <button
                                    onClick={() => selectSession(s.id)}
                                    aria-current={isActive ? "page" : undefined}
                                    aria-label={
                                      parentTitle
                                        ? [
                                            t("sidebar.forkedFrom", {
                                              title: s.title,
                                              parent: parentTitle,
                                            }),
                                            // The label overrides content: keep the marker announced.
                                            heldCount > 0 &&
                                              tOut("held.chatMark", "Paused: {{count}} items need your OK", {
                                                count: heldCount,
                                              }),
                                          ]
                                            .filter(Boolean)
                                            .join(", ")
                                        : undefined
                                    }
                                    className={`w-full text-left text-sm rounded-md px-3 py-2 pr-8 transition-colors ${
                                      isActive
                                        ? "bg-accent text-accent-foreground"
                                        : "hover:bg-accent/50 text-foreground"
                                    }`}
                                  >
                                    <span className="flex items-center gap-1.5 min-w-0">
                                      {isFork && (
                                        <CornerDownRight
                                          className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                                          aria-hidden
                                        />
                                      )}
                                      {s.idea_id != null && (
                                        <Lightbulb
                                          data-testid={`idea-marker-${s.id}`}
                                          className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                                          aria-hidden
                                        />
                                      )}
                                      <span className="truncate">{s.title}</span>
                                      {s.idea_id != null && (
                                        <span className="sr-only">
                                          {t("sidebar.fromIdea")}
                                        </span>
                                      )}
                                      <span className="ml-auto flex shrink-0">
                                        <HeldMark count={heldCount} />
                                      </span>
                                    </span>
                                  </button>
                                  <button
                                    type="button"
                                    onClick={(e) => handleDelete(e, s.id)}
                                    aria-label={t("sidebar.deleteChat", { title: s.title })}
                                    className="absolute right-1 top-1/2 -translate-y-1/2 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 transition-opacity p-1 rounded hover:bg-destructive/10"
                                  >
                                    <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
                                  </button>
                                </li>
                              );
                                })}
                              </ul>
                            </div>
                          </div>
                        </section>
                      );
                    })}
                  </div>
                )}
              </nav>
            </>
          ) : (
            <div className="flex-1 min-h-0">
              <ReferenceSourcesPanel frontendSessionId={activeSessionId} />
            </div>
          )}
        </>
      )}

      {/* Footer — account menu (language / theme / settings / about). */}
      <div className="mt-auto border-t border-border p-2">
        <AccountMenu collapsed={collapsed} onOpenSettings={openSettings} />
      </div>
    </div>
  );
}
