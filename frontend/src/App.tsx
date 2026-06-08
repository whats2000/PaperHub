import { Shell } from "@/components/layout/Shell";
import { Sidebar } from "@/components/layout/Sidebar";
import { SettingsModal } from "@/components/settings/SettingsModal";
import { Toaster } from "@/components/ui/sonner";
import { ChatPage } from "@/pages/ChatPage";

function App() {
  return (
    <>
      <Shell sidebar={<Sidebar />}>
        <ChatPage />
      </Shell>
      <SettingsModal />
      <Toaster />
    </>
  );
}

export default App;
