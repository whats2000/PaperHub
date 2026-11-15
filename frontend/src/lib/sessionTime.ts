/** Now as an ISO timestamp (a chat's last-activity stamp). */
export function nowIso(): string {
  return new Date().toISOString();
}

/**
 * A backend timestamp as ISO. SQLite's `datetime('now')` is UTC written
 * without a zone ("2026-11-12 08:30:00"), so a zone-less value is read as
 * UTC. Returns undefined for a missing or unparseable value.
 */
export function parseBackendTime(raw: string | null | undefined): string | undefined {
  if (!raw) return undefined;
  const s = raw.trim().replace(" ", "T");
  const zoned = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(s) ? s : `${s}Z`;
  const ms = Date.parse(zoned);
  return Number.isNaN(ms) ? undefined : new Date(ms).toISOString();
}

/** The later of two ISO timestamps; either may be missing. */
export function laterIso(a: string | undefined, b: string | undefined): string | undefined {
  if (!a) return b;
  if (!b) return a;
  return Date.parse(a) >= Date.parse(b) ? a : b;
}
