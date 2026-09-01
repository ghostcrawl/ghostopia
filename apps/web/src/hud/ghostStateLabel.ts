// ghostopia web — ghost-appropriate DISPLAY labels for the coarse engine state.
//
// The server's authoritative state strings (WALKING / RETURNING_HOME / EXTRACTING / …) are the
// engine's own vocabulary — literal and un-ghostly ("walking" makes no sense for a ghost, 196).
// This maps each to an on-theme phrase for the roster + inspector DISPLAY ONLY; the server
// state is untouched (the world sprite, metrics, and management all still key off the raw
// string). Anything unmapped falls back to a humanized lower-case form, so a new/rare state
// never renders a raw SCREAMING_CASE token.

const GHOST_STATE_LABELS: Record<string, string> = {
  IDLE: "resting",
  RECEIVING_TASK: "summoned",
  WALKING: "drifting",
  RETURNING_HOME: "drifting home",
  AT_WORKSTATION: "haunting",
  OPENING_BROWSER: "conjuring",
  NAVIGATING: "haunting",
  SEARCHING: "seeking",
  READING: "poring over",
  SCROLLING: "sifting",
  EXTRACTING: "gathering",
  PROCESSING: "divining",
  WAITING: "lingering",
  RETRYING: "rallying",
  ERROR: "spooked",
  COMPLETED: "delivered",
  // non-schema strings the live pool/orchestrator/management surface can still emit
  WORKING: "haunting",
  CANCELLED: "dismissed",
  RETARGETED: "beckoned",
};

/** The on-theme DISPLAY label for a coarse engine state (falls back to a humanized form). */
export function ghostStateLabel(state: string | null | undefined): string {
  if (!state) return "resting";
  const mapped = GHOST_STATE_LABELS[state];
  if (mapped) return mapped;
  return state.toLowerCase().replace(/_/g, " ");
}
