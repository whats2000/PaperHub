import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { getOutboundDestinations } from "@/lib/api";

let trusted: Promise<Set<string>> | null = null;

/** Hosts the deployer marked as the trusted search service (fetched once). */
function trustedHosts(): Promise<Set<string>> {
  trusted ??= getOutboundDestinations()
    .then((rows) => new Set(rows.filter((r) => r.source === "trusted_search").map((r) => r.host)))
    .catch(() => {
      trusted = null; // try again next time
      return new Set<string>();
    });
  return trusted;
}

/** The destination as the user reads it: "public web search", "trusted
 *  search", "Semantic Scholar", otherwise the host itself. */
export function useDestinationLabel(destination: string): string {
  const { t } = useTranslation("chat");
  const known = destination === "public-web-search" || destination === "api.semanticscholar.org";
  const [isTrusted, setIsTrusted] = useState(false);
  useEffect(() => {
    if (known) return;
    let live = true;
    void trustedHosts().then((hosts) => {
      if (live) setIsTrusted(hosts.has(destination));
    });
    return () => {
      live = false;
    };
  }, [destination, known]);
  if (destination === "public-web-search") {
    return t("outbound.dest.publicSearch", "public web search");
  }
  if (destination === "api.semanticscholar.org") {
    return t("outbound.dest.semanticScholar", "Semantic Scholar");
  }
  return isTrusted ? t("outbound.dest.trusted", "trusted search") : destination;
}

/** Test seam: forget the cached trusted-host lookup. */
export function resetDestinationCache(): void {
  trusted = null;
}
