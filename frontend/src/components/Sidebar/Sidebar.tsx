/**
 * Fixed-width left sidebar.
 *
 * Phase A: PaperHub header + New Chat button.
 * Phase B: Project list, paper library navigation.
 */

import { useChatStore } from "../../store/chat";

export function Sidebar() {
  const reset = useChatStore((s) => s.reset);

  return (
    <aside className="flex h-full w-64 flex-shrink-0 flex-col border-r border-neutral-800 bg-neutral-900 p-4">
      <div className="mb-6">
        <h1 className="text-xl font-semibold text-neutral-100">PaperHub</h1>
        <p className="mt-1 text-xs text-neutral-500">AI research assistant</p>
      </div>

      <button
        onClick={reset}
        className="flex items-center gap-2 rounded-md border border-neutral-700 bg-neutral-800 px-3 py-2 text-sm text-neutral-200 transition hover:bg-neutral-700 hover:text-white"
      >
        <span>+</span>
        <span>New chat</span>
      </button>

      {/* Phase B: project list */}
    </aside>
  );
}
