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
  // the onboarding tour.
  const fetchReadiness = useSettingsStore((s) => s.fetchReadiness);
  useEffect(() => {
    void fetchReadiness();
  }, [fetchReadiness]);

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
