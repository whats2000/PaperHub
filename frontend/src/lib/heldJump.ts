import { useChatStore } from "@/store/chat";
import { useNavStore } from "@/store/nav";
import { useSettingsStore } from "@/store/settings";

/**
 * Open the chat with this backend session id (closing Settings) and, with a
 * held id, land on that held search's card: the card scrolls itself into
 * view, rings and focuses once it is mounted (`focusHeldId`). Returns false
 * when the chat is not on this device.
 */
export function openChatAtHeld(backendSessionId: number, heldId?: string): boolean {
  const s = useChatStore
    .getState()
    .sessions.find((x) => x.backend_session_id === backendSessionId);
  if (!s) return false;
  useChatStore.getState().selectSession(s.id);
  useNavStore.getState().setPage("chat");
  if (heldId) useNavStore.getState().setFocusHeldId(heldId);
  useSettingsStore.getState().close();
  return true;
}
