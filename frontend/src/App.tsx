import { useEffect } from "react";

import { Shell } from "@/components/layout/Shell";
import { Sidebar } from "@/components/layout/Sidebar";
import { SettingsModal } from "@/components/settings/SettingsModal";
import { WelcomeModal } from "@/components/settings/WelcomeModal";
import { Toaster } from "@/components/ui/sonner";
import { ChatPage } from "@/pages/ChatPage";
import { useSettingsStore } from "@/store/settings";

function App() {
  // Probe the first-run config gate once on boot — drives the composer lock and
  // the onboarding tour. Uses the verified cache (skips the live ping) when the
  // config was confirmed good recently.
  const ensureReadiness = useSettingsStore((s) => s.ensureReadiness);
  useEffect(() => {
    void ensureReadiness();
  }, [ensureReadiness]);

  return (
    <>
      <Shell sidebar={<Sidebar />}>
        <ChatPage />
      </Shell>
      <SettingsModal />
      <WelcomeModal />
      <Toaster />
    </>
  );
}

export default App;
