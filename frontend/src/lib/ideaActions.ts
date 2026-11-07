import { useTranslation } from "react-i18next";

import { postIdeationIdeaChat } from "@/lib/api";
import i18n from "@/lib/i18n";
import { useChatStore } from "@/store/chat";
import { useIdeationStore } from "@/store/ideation";
import { useNavStore } from "@/store/nav";
import {
  DISMISS_REASONS,
  FULL_TEXT_SECTION,
  type DismissReason,
  type GraphResponse,
  type Idea,
} from "@/types/ideation";

export const REASON_FALLBACK: Record<DismissReason, string> = {
  already_tried: "Already tried",
  not_feasible: "Not feasible",
  analogy_fails: "Analogy doesn't hold",
  not_novel: "Not novel",
  other: "Other",
};

/** A cited section's name for display: a sectionless document's
 *  `FULL_TEXT_SECTION` in the UI language, any real section name as is. */
export function sectionLabel(section: string): string {
  return section === FULL_TEXT_SECTION ? i18n.t("ideas:section.fullText", "Full text") : section;
}

/** The note saves this long after the last keystroke. */
export const NOTE_DEBOUNCE_MS = 500;

const isReason = (r: string): r is DismissReason => (DISMISS_REASONS as readonly string[]).includes(r);

/** The idea's dismissal reasons in the UI language, comma-joined. */
export function useReasonText(): (reasons: string[]) => string {
  const { t } = useTranslation("ideas");
  return (reasons) =>
    reasons
      .map((r) => (isReason(r) ? t(`dismiss.reasons.${r}`, REASON_FALLBACK[r]) : r))
      .join(t("dismiss.joiner", ", "));
}

/**
 * The idea chat's opening message in the UI language (the server's `prefill`
 * is English). Null when the graph lacks a document it names, so the caller
 * falls back to the server text.
 */
export function ideaChatPrefill(idea: Idea, graph: GraphResponse | null): string | null {
  if (!graph) return null;
  const title = (docId: number) => graph.documents.find((d) => d.id === docId)?.title;
  const source = title(idea.source_doc);
  if (!source) return null;
  const common = { title: idea.title, source, sourceSection: sectionLabel(idea.source_section), rationale: idea.rationale };
  if (idea.target_doc === null) {
    const problem = graph.problem?.text;
    if (!problem) return null;
    return i18n.t("ideas:chat.prefillProblem", { ...common, problem });
  }
  const target = title(idea.target_doc);
  if (!target) return null;
  return i18n.t("ideas:chat.prefillExplore", { ...common, target, targetSection: sectionLabel(idea.target_section ?? "") });
}

/**
 * Show a chat for the idea on the chat page. With `reuseLatest`, the idea's
 * most recent chat is reopened when it is in the sidebar; otherwise the
 * backend opens a new linked session (both documents attached) and its
 * opening message is put in the composer, editable and unsent.
 * Resolves to the local session id.
 */
export async function openIdeaChat(idea: Idea, reuseLatest: boolean): Promise<number> {
  const chat = useChatStore.getState();
  const latest = idea.session_ids.at(-1);
  if (reuseLatest && latest !== undefined) {
    const local = chat.sessions.find((s) => s.backend_session_id === latest);
    if (local) {
      chat.selectSession(local.id);
      return local.id;
    }
  }
  const res = await postIdeationIdeaChat(idea.id);
  const ideation = useIdeationStore.getState();
  ideation.addIdeaSession(idea.id, res.session_id);
  const id = chat.addForkedSession(res.session_id, res.title, null, idea.id);
  chat.requestComposerText(ideaChatPrefill(idea, ideation.graph) ?? res.prefill);
  useNavStore.getState().setPage("chat");
  return id;
}

