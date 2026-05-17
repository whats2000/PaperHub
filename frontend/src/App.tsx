import { ChatPane } from "./components/ChatPane/ChatPane";
import { Sidebar } from "./components/Sidebar/Sidebar";

export default function App() {
  return (
    <div className="flex h-screen overflow-hidden bg-neutral-950 text-neutral-100">
      <Sidebar />
      <ChatPane />
    </div>
  );
}
