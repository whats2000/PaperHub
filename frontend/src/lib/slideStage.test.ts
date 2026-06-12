import { describe, expect, it } from "vitest";
import type { ToolCallRecord } from "@/types/domain";
import { slideStageKey } from "./slideStage";

function makeRecord(tool: string): ToolCallRecord {
  return {
    run_id: 1,
    branch: "",
    step_index: 1,
    parent_step: null,
    agent: "report",
    tool,
    model: "",
    args_redacted_json: null,
    result_summary_json: null,
    latency_ms: 0,
    token_in: 0,
    token_out: 0,
    status: "ok",
    error: null,
  };
}

describe("slideStageKey", () => {
  it("returns stage.warmup for empty trace", () => {
    expect(slideStageKey(undefined)).toBe("stage.warmup");
    expect(slideStageKey([])).toBe("stage.warmup");
  });

  it("maps report:reading → stage.reading", () => {
    expect(slideStageKey([makeRecord("report:reading")])).toBe("stage.reading");
  });

  it("maps report:planning → stage.planning", () => {
    expect(slideStageKey([makeRecord("report:planning")])).toBe("stage.planning");
  });

  it("maps report:outline → stage.planning (outline substring)", () => {
    expect(slideStageKey([makeRecord("report:outline")])).toBe("stage.planning");
  });

  it("maps report:drafting → stage.draft (draft substring)", () => {
    expect(slideStageKey([makeRecord("report:drafting")])).toBe("stage.draft");
  });

  it("maps report:compiling → stage.compile (compile substring)", () => {
    expect(slideStageKey([makeRecord("report:compiling")])).toBe("stage.compile");
  });

  it("uses the last record in the trace", () => {
    const trace = [makeRecord("report:reading"), makeRecord("report:planning")];
    expect(slideStageKey(trace)).toBe("stage.planning");
  });

  it("maps existing stages correctly", () => {
    expect(slideStageKey([makeRecord("report:resolve")])).toBe("stage.resolve");
    expect(slideStageKey([makeRecord("report:understand")])).toBe("stage.understand");
    expect(slideStageKey([makeRecord("report:narrate")])).toBe("stage.narrate");
    expect(slideStageKey([makeRecord("report:compile")])).toBe("stage.compile");
    expect(slideStageKey([makeRecord("report:notes")])).toBe("stage.notes");
  });
});
