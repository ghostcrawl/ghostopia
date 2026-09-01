// ghostopia web — the client's view of "is the workforce running?".
//
// The workforce is "running" whenever any workforce ghost is present in the live world. The
// AUTHORITATIVE set is server-owned (gc_event_source _has_live_workforce / _stop_workforce):
// the featured workforce-* / dept-* ghosts AND the background baton stage-* ghosts. The client
// predicate MUST match it exactly — if the transient dept-* ghosts are absent between loop
// cycles while stage-* ghosts are live, omitting stage- flips the Run⇄Stop toggle and the
// onboarding CTA to the wrong state (the overlay reappears over a running workforce).

/** The id prefixes that mark a live-workforce ghost (mirrors the server's authoritative set). */
export const WORKFORCE_GHOST_PREFIXES = ["workforce-", "dept-", "stage-"] as const;

/** True when a ghost id belongs to the workforce (featured OR background baton). */
export function isWorkforceGhostId(id: string): boolean {
  return WORKFORCE_GHOST_PREFIXES.some((p) => id.startsWith(p));
}

/** True when ANY ghost in the world belongs to the workforce → the workforce is running. */
export function anyWorkforceGhost(ghostIds: Iterable<string>): boolean {
  for (const id of ghostIds) {
    if (isWorkforceGhostId(id)) return true;
  }
  return false;
}
