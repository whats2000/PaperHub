import { describe, expect, it } from "vitest";

import type { SettingsModelCheck, SettingsReadiness } from "./api";
import { hasBlockingConfigIssue, isTransientError } from "./readiness";

function check(over: Partial<SettingsModelCheck>): SettingsModelCheck {
  return { model: "gemini/x", key_ok: true, missing_keys: [], error: null, detail: null, ...over };
}

function readiness(small: SettingsModelCheck, flagship: SettingsModelCheck): SettingsReadiness {
  return {
    ready: small.key_ok && flagship.key_ok,
    credentials_set: true,
    models: { small, flagship },
  };
}

describe("hasBlockingConfigIssue", () => {
  it("blocks when a key is missing", () => {
    const r = readiness(check({ key_ok: false, missing_keys: ["GEMINI_API_KEY"] }), check({}));
    expect(hasBlockingConfigIssue(r)).toBe(true);
  });

  it("blocks when a key is rejected (auth)", () => {
    const r = readiness(check({}), check({ key_ok: false, error: "AuthenticationError" }));
    expect(hasBlockingConfigIssue(r)).toBe(true);
  });

  it("does NOT block on a transient ping failure", () => {
    // The idle-reload case: re-ping fails with a connection error, no missing
    // key, key not rejected — must not lock the composer / pop the tour.
    const r = readiness(check({ key_ok: false, error: "APIConnectionError" }), check({}));
    expect(hasBlockingConfigIssue(r)).toBe(false);
  });

  it("does NOT block on a transient 'model not found' after idle", () => {
    const r = readiness(check({}), check({ key_ok: false, error: "NotFoundError" }));
    expect(hasBlockingConfigIssue(r)).toBe(false);
  });

  it("does not block when everything is ready", () => {
    expect(hasBlockingConfigIssue(readiness(check({}), check({})))).toBe(false);
  });
});

describe("isTransientError", () => {
  it("classifies network/provider hiccups as transient", () => {
    expect(isTransientError("APIConnectionError")).toBe(true);
    expect(isTransientError("RateLimitError")).toBe(true);
  });
  it("classifies config errors as non-transient", () => {
    expect(isTransientError("AuthenticationError")).toBe(false);
    expect(isTransientError(null)).toBe(false);
    expect(isTransientError(undefined)).toBe(false);
  });
});
