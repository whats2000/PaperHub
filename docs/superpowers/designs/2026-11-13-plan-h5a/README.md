# Plan H5a design prototype

The signed-off interactive design for Plan H5a (2026-11-13) — the **visual base for the H5a implementation plan**: match its layout, states, copy and motion. The settled decisions behind it are in SRS §III-10.5 (H5a).

- **View it:** <https://claude.ai/artifact/Kaa3uA31NAXusS4kPSQ7Zw> (private artifact; ask the owner for access). It covers three areas:
  - **Chat:** a rewritten search ("Search sent as …" chip with what was written, why and where), a held search card ("This search needs your OK": Send as shown / Edit, then send / Don't send), an Ask-first action card ("Confirm this action": Confirm / Dismiss), a blocked search line, and the sidebar "Held searches" entry with its count.
  - **Approval mode decides what asks.** It follows the common agent-app pattern, where you approve the agent's tool call rather than the requests the tool makes internally. The composer's mode chip switches the example turn in the prototype.
    - **Ask first:** the agent's `search_papers` call pauses on "Allow this search?" before anything leaves. The card shows the query, where it goes and why you're asked, plus a note that follow-up queries the search writes are still checked for sensitive terms and private material. The choices are:
      - **Allow**;
      - **Allow searches in this chat** (later searches stop asking; actions still ask). When parallel searches each wait on a card, it also allows the others still waiting, and its label says so ("· also allows 2 waiting"); each of them settles as "Allowed: … · searches are allowed in this chat". Held cards and actions keep waiting;
      - **Edit, then allow**;
      - **Don't allow**, which opens an optional "tell the agent what to do instead" box the agent continues with.
    - **Auto:** searches run. The privacy guard checks every outgoing query, and only a flagged one pauses on "This search needs your OK" with the reason. Actions are auto-approved with Undo.
    - **Both modes:** a sensitive term blocks the query outright.
  - **Activity trace.** The trace is a timeline with one node per thing the agent did, in plain language: "Read “2 Inspection setup” in TR-0412…", "Searched papers for “…”", "Removed “…” from this chat", "Wrote the answer". Its parts:
    - **Node state:** each node's marker shows done, running (spinner), waiting for your OK (amber clock, with "Go to card"), skipped (you declined, or Stop) or failed. The duration sits on the right.
    - **Search rows:** sub-steps roll up into one line, e.g. "Found 3 papers. 3 queries sent (1 rewritten first), 1 not sent, 1 timed out." Its "Queries and details" disclosure lists every query that left or tried to leave, with destination and outcome. A sub-step failure, such as one timed-out web query, never turns the node red; only a failed tool call does.
    - **Details:** every node's Details shows the technical record (tool id, the agent step and model that called it, status, internal steps).
    - **Header:** the toggle reads "Worked 10.6 s: read 2 sections, ran 1 search", "Paused, waiting for your OK on 2 items" or "Stopped…".
    - **Raw steps:** a quiet "View raw steps (N)" link below the last node shows the raw step list, with "Back to activity view" in the same spot. Nothing else sits on the toggle's row, and the toggle has no hover popup.
    - **Built from real data:** the prototype builds the timeline only from rows shaped like real `tool_calls` records, so its `buildTimeline` / `nodeView` / `queries` functions are the mapping the app implements:
      - a `tool:*` row starts a node, and the rows after it (`paper_search:*`, `outbound:review`, `agent:await_approval`, `agent:approve`) belong to it;
      - `agent:step` rows feed Details, and the final one is "Wrote the answer".
      - In the real trace, parallel tool calls interleave their sub-steps, so each sub-step row records the id of the tool call it belongs to.
  - **A turn that needs your OK pauses.** It waits at its cards, with no answer and no time limit: the trace row reads "paused · waiting for your OK", a "Paused · N items wait for your OK" line stands where the answer will go, and Stop stays available. Each decision updates its card and trace row. When the last card is decided, the same run resumes and writes one answer covering what was sent, skipped, confirmed or dismissed. There is no follow-up turn. Stop ends the run and nothing waiting is sent or run. A paused chat shows an amber marker with the count on its sidebar row. When a waiting card is scrolled out of view, a "Paused · waiting for your OK" pill above the composer points up or down to it, scrolls to it and rings it briefly. Searches and actions use the same signals.
  - **Sidebar chats:** grouped by last activity, newest first (Today, Yesterday, Previous 7 days, Previous 30 days, then month); a new chat starts at the top of Today; forks stay under their parent; groups collapse toward their header.
  - **Settings nav:** every section has an icon (Models & providers `cpu`, Agents & memory `bot`, Integrations `plug`, Privacy & outbound `shield`, System `settings-2`, Outbound log `scroll-text`).
  - **Settings › Privacy & outbound:** the cloud-model notice (one line inside the scrolling body, "Show which" opens the list grouped by model — it never pins over the pane; a model set only in the server environment, such as the Marker accuracy pass or agent vision, shows a readable name marked "server environment", never the raw variable name), deployer-locked rows ("Set by the deployer"), the sensitive-term editor, the private-upload default, web search in two ways (trusted search service; public web search behind an inline warning), allowed destinations, log retention.
  - **Outbound log:** a stacked list that wraps inside the pane, never a wide table with a horizontal scroll (line 1 decision · destination · time; line 2 the text, a rewrite's original struck through; line 3 who decided · the chat as a link). Everything sent has filters; the Held tab uses the same rows: a background job's search has Send / Discard, a chat's search has "Open in chat to decide", which opens the chat at its card.
- **The prototype, not the production code.** One hand-built HTML page using the app's real tokens from `frontend/src/index.css`, Geist and lucide icons, and the shapes of `SettingsModal.tsx`, `ConfirmActionCard.tsx` and `AgentEventChip.tsx`. Production UI is built from the real React components with `t()` strings in all four locales.

**Revision 2026-11-14** (after the first live UI check): the cloud-model notice had pinned a 22-row list over the pane; the log tables overflowed sideways; a held search had no way back to its chat and no marker in the chat list; the sidebar listed chats oldest first with no time groups; settings sections now all carry an icon.

**Revision 2026-11-15:** approval pauses the run, the way other agent apps do. Before, a held search or an Ask-first action returned "waiting" to the agent, which answered anyway, and approving started a follow-up turn. Now the turn waits at its cards and resumes in place. Later the same day, the approval mode was made to govern searches: Ask first asks before the agent's search call, and Auto relies on the guard, which explains a flagged query. After the live check: the Technical view switch (it overlapped the message actions) became a "View raw steps" link after the last node, the summary's tool-id tooltip went, citation markers got a readable fixed size, and "Allow searches in this chat" now also allows the other waiting search cards.

## Files

| File | What it is |
| --- | --- |
| `h5a_prototype.html` | The prototype source, published as-is (the artifact host adds the document skeleton). All UI, sample data and interactions live here. |
