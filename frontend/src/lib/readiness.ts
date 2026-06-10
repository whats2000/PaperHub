import type { SettingsReadiness } from "./api";

// Provider errors that mean the user genuinely must fix their config: the key
// is absent or rejected. Everything else a pre-flight ping can return — a
// connection drop, a timeout, a rate limit, a momentary "model not found"
// after the tab idled — is TRANSIENT and must not gate the UI.
const HARD_ERRORS = new Set(["AuthenticationError", "PermissionDeniedError"]);

/**
 * Whether readiness reflects a config problem the user must fix (missing or
 * rejected provider key) — as opposed to a transient ping failure.
 *
 * The composer lock and the onboarding tour gate on THIS, not on `!ready`: a
 * transient blip (e.g. the readiness re-ping after the site idled and the
 * connection went cold) shouldn't lock the app or re-pop the tour. A real send
 * would just surface the error normally and a retry usually clears it. The
 * Settings panel still shows the per-slot reason for any failure.
 */
export function hasBlockingConfigIssue(r: SettingsReadiness): boolean {
  const models = [r.models.small, r.models.flagship];
  return models.some(
    (m) =>
      !m.key_ok &&
      (m.missing_keys.length > 0 || (m.error != null && HARD_ERRORS.has(m.error))),
  );
}

// litellm/openai exception classes that signal a transient failure (network /
// provider hiccup), not a config mistake. A model field that failed with one of
// these shows a "couldn't reach the provider" hint rather than "model unusable".
const TRANSIENT_ERRORS = new Set([
  "APIConnectionError",
  "APITimeoutError",
  "Timeout",
  "RateLimitError",
  "InternalServerError",
  "ServiceUnavailableError",
  "APIError",
]);

export function isTransientError(error?: string | null): boolean {
  return error != null && TRANSIENT_ERRORS.has(error);
}
