// ghostopia ghost-renderer — grave-rest presence (196).
//
// A resting ghost should NOT hover above its gravestone. When it settles into its
// home grave (state IDLE) it DE-MATERIALIZES: its body alpha ramps down to ~0 (it
// sinks into the stone) and the float-bob is suppressed, so it reads as "gone into
// the grave", not floating above it. When it DEPARTS the grave (leaves IDLE to walk
// to a workstation / section) it MATERIALIZES: its body alpha ramps back up 0->1 as
// it rises, then it walks.
//
// The presence math is a tiny pure state machine (unit-tested); the render loop owns
// the PixiJS side (container.alpha + the fog flourish + the bob-suppress UI flag).
// Reduced motion collapses the ramps to a steady faded/visible state (no motion, no
// motes — the render loop's flourish already early-returns under reduced motion).
//
// Imports only the GhostState contract type — NO PixiJS.

import type { GhostState } from "./contract.js";

/** Duration (ms) of the sink/rise alpha ramp — matched to the spawn/dissolve flourish. */
export const REST_FADE_MS = 460;

/** Body alpha a ghost sinks to once fully at grave-rest (de-materialized INTO the stone). */
export const REST_SUNK_ALPHA = 0;

/**
 * Under `prefers-reduced-motion` a resting ghost freezes to this steady faded alpha instead of
 * ramping to 0 — dim enough to read as "at the grave", but no sink animation and no motes.
 */
export const REST_REDUCED_ALPHA = 0.4;

/** The presence phases a ghost body moves through around its home grave. */
export type PresencePhase = "visible" | "sinking" | "sunk" | "rising";

/** A ghost's current grave-rest presence: the phase + the body alpha to apply this frame. */
export interface PresenceState {
  phase: PresencePhase;
  /** current body (container) alpha, 0..1. */
  alpha: number;
}

/** The per-frame result of {@link stepPresence}. */
export interface PresenceStep {
  /** the advanced presence state (feed back in next frame). */
  state: PresenceState;
  /** a fog flourish to spawn at the grave THIS frame on a transition, else null. */
  flourish: "materialize" | "dissolve" | null;
  /** true while the ghost is sinking/sunk into the grave -> suppress its float-bob (no hovering). */
  suppressBob: boolean;
}

/**
 * Is this ghost state a TRUE grave-rest (settled into its home stone)? Only `IDLE` counts —
 * a between-walk pause stays `WALKING`/`RETURNING_HOME` while the server chains waypoints, and
 * a working phase is never a rest. Pure predicate so the "reserved for true rest" contract is
 * testable without PixiJS. Byte-parity with `restingZzzVisible` (GhostSprite).
 */
export function isGraveRestState(state: GhostState): boolean {
  return state === "IDLE";
}

/**
 * The presence a NEWLY-SEEN ghost starts at, keyed on its state. A ghost first seen already at
 * grave-rest starts SUNK (in the stone — no spurious dissolve on first appearance); an active
 * ghost starts fading in (rising from 0), reproducing the spawn materialize. Under reduced motion
 * there is no fade: a resting ghost starts at the steady faded alpha and an active one starts
 * fully visible.
 */
export function initialPresence(state: GhostState, reduceMotion: boolean): PresenceState {
  if (isGraveRestState(state)) {
    return { phase: "sunk", alpha: reduceMotion ? REST_REDUCED_ALPHA : REST_SUNK_ALPHA };
  }
  if (reduceMotion) return { phase: "visible", alpha: 1 };
  return { phase: "rising", alpha: 0 };
}

/**
 * Advance a ghost's grave-rest presence by one clamped frame delta.
 *
 * `isIdle` (from {@link isGraveRestState}) is the desired rest target. Entering rest from a
 * non-resting phase starts a SINK (dissolve flourish + alpha ramp down); departing rest from a
 * resting phase starts a RISE (materialize flourish + alpha ramp up). Under reduced motion the
 * transition snaps to its steady endpoint (no ramp). Pure — no PixiJS, deterministic in `dtMs`.
 */
export function stepPresence(
  prev: PresenceState,
  isIdle: boolean,
  dtMs: number,
  reduceMotion: boolean,
): PresenceStep {
  let phase = prev.phase;
  let alpha = prev.alpha;
  let flourish: "materialize" | "dissolve" | null = null;
  const restFloor = reduceMotion ? REST_REDUCED_ALPHA : REST_SUNK_ALPHA;
  const resting = phase === "sinking" || phase === "sunk";

  // ---- transition detection (only on an actual enter/leave of the resting phases) ----
  if (isIdle && !resting) {
    // settle into the grave: de-materialize
    flourish = "dissolve";
    if (reduceMotion) {
      phase = "sunk";
      alpha = restFloor;
    } else {
      phase = "sinking";
    }
  } else if (!isIdle && resting) {
    // depart the grave: materialize
    flourish = "materialize";
    if (reduceMotion) {
      phase = "visible";
      alpha = 1;
    } else {
      phase = "rising";
    }
  }

  // ---- advance the active ramp toward its endpoint ----
  if (phase === "sinking") {
    alpha -= dtMs / REST_FADE_MS;
    if (alpha <= restFloor) {
      alpha = restFloor;
      phase = "sunk";
    }
  } else if (phase === "rising") {
    alpha += dtMs / REST_FADE_MS;
    if (alpha >= 1) {
      alpha = 1;
      phase = "visible";
    }
  } else if (phase === "sunk") {
    alpha = restFloor;
  } else {
    alpha = 1;
  }

  return {
    state: { phase, alpha },
    flourish,
    suppressBob: phase === "sinking" || phase === "sunk",
  };
}
