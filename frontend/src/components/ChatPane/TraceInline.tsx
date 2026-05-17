/**
 * Collapsible disclosure showing the tool-call trace steps.
 *
 * Phase A: flat list of ToolCall rows.
 * Phase B: DAG layout with parent/child relationships.
 */

import { useState } from "react";
import type { ToolCall } from "../../api/types";

interface Props {
  steps: ToolCall[];
}

export function TraceInline({ steps }: Props) {
  const [open, setOpen] = useState(false);

  if (steps.length === 0) return null;

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
      className="mt-1 rounded-md border border-neutral-700 bg-neutral-900 text-xs"
    >
      <summary className="cursor-pointer select-none px-3 py-2 text-neutral-400 hover:text-neutral-200">
        {steps.length} tool step{steps.length !== 1 ? "s" : ""} — click to expand
      </summary>
      <ul className="divide-y divide-neutral-800">
        {steps.map((step) => (
          <li key={`${step.run_id}-${step.step_index}`} className="px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="font-mono text-neutral-300">
                [{step.agent}] {step.tool}
              </span>
              <span
                className={`rounded px-1 text-[10px] ${
                  step.status === "ok"
                    ? "bg-green-900 text-green-300"
                    : step.status === "rejected"
                      ? "bg-yellow-900 text-yellow-300"
                      : "bg-red-900 text-red-300"
                }`}
              >
                {step.status}
              </span>
            </div>
            <div className="mt-0.5 text-neutral-500">{step.latency_ms}ms</div>
            {step.error && <div className="mt-1 text-red-400">{step.error}</div>}
          </li>
        ))}
      </ul>
    </details>
  );
}
