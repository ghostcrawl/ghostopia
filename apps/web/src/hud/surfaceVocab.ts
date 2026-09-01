// ghostopia web — ghost-appropriate DISPLAY labels for the customer-surface VOCABULARY.
//
// The sibling `ghostStateLabel.ts` themes the coarse ENGINE STATE strings; this file extends the
// SAME display-only pattern to the rest of the surface copy — the engine/UI words that leak
// through the HUD, legend, hints and panel triggers ("workstation", "walk", "mission",
// "home", "drag to pan"). The server's authoritative strings, section ids and env keys are NEVER
// renamed — only the RENDERED label passes through `surfaceLabel()`.
//
// Clarity guardrail (non-negotiable): words that are already clear AND on-theme
// ("department", "findings", "Data Graveyard", "roster") are deliberately NOT in the map — an
// unmapped phrase returns VERBATIM (not humanized-uppercase like ghostStateLabel), because this
// copy is already lowercase UI text and theme must never win over comprehension.

export const SURFACE_LABELS: Record<string, string> = {
  // a department's work-spot (roster/legend/inspector)
  workstation: "haunt",
  // motion words (parity with the shipped ghostStateLabel WALKING→"drifting")
  walk: "drift",
  walking: "drift",
  // a dispatched unit of work
  mission: "summoning",
  // the noun form used in legend/hint rows (state IDLE already themes to "resting")
  home: "resting place",
  // the top-left HUD hint: keeps "drag" (still 100% clear) and trades the technical camera
  // term "pan" for the on-theme "drift"
  "drag to pan": "drag to drift the view",
  // the residual workforce trigger label (S6): if run_department_workforce needs a user-facing
  // button, it reads as summoning the departments
  "run workforce": "summon the departments",
};

/**
 * The on-theme DISPLAY label for a customer-surface word/phrase. Display-only: server strings,
 * section ids and env keys are untouched. An unmapped phrase returns VERBATIM (clarity words like
 * "department"/"findings" pass straight through — never obscured). Lookup is case-insensitive so a
 * stray Cased literal ("Run workforce", "WORKSTATION") still themes to the same value.
 */
export function surfaceLabel(s: string | null | undefined): string {
  if (!s) return s ?? "";
  const exact = SURFACE_LABELS[s];
  if (exact) return exact;
  const lowered = SURFACE_LABELS[s.toLowerCase()];
  if (lowered) return lowered;
  return s;
}
