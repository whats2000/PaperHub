import { useEffect } from "react";

import { ChangelogModal } from "@/components/about/ChangelogModal";
import { Shell } from "@/components/layout/Shell";
import { Sidebar } from "@/components/layout/Sidebar";
import { SettingsModal } from "@/components/settings/SettingsModal";
import { WelcomeModal } from "@/components/settings/WelcomeModal";
import { Toaster } from "@/components/ui/sonner";
import { useFeedbackBadge } from "@/hooks/useFeedbackBadge";
import { ChatPage } from "@/pages/ChatPage";
import { FeedbackPage } from "@/pages/FeedbackPage";
import { IdeasPage } from "@/pages/IdeasPage";
import { useNavStore } from "@/store/nav";
import { useSettingsStore } from "@/store/settings";
import { useVersionStore } from "@/store/version";

function App() {
  const page = useNavStore((s) => s.page);
  // Sidebar Feedback badge = confirmation queue length (mount, each turn, 60 s).
  useFeedbackBadge();
  // Probe the first-run config gate once on boot — drives the composer lock and
  // the onboarding tour. Uses the verified cache (skips the live ping) when the
  // config was confirmed good recently.
  const ensureReadiness = useSettingsStore((s) => s.ensureReadiness);
  useEffect(() => {
    void ensureReadiness();
  }, [ensureReadiness]);

  useEffect(() => {
    void useVersionStore.getState().fetchVersion();
  }, []);

  return (
    <>
      <Shell sidebar={<Sidebar />}>
        {/* Chat stays mounted (hidden, not unmounted) so a streaming answer and
            the citation canvas survive a page switch. `[&[hidden]]:hidden`
            keeps the hidden attribute winning over the `flex` utility. The
            fade replays on return: lifting display:none restarts the CSS
            animation, matching the Ideas/Feedback pages' entry. */}
        <div
          hidden={page !== "chat"}
          className="flex flex-1 min-h-0 flex-col animate-in fade-in-0 duration-150 motion-reduce:animate-none [&[hidden]]:hidden"
        >
          <ChatPage />
        </div>
        {page === "ideas" && <IdeasPage />}
        {page === "feedback" && <FeedbackPage />}
      </Shell>
      <SettingsModal />
      <WelcomeModal />
      <ChangelogModal />
      <Toaster />
    </>
  );
}

export default App;
