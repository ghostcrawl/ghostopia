// ghostopia ghost-renderer — the client-side mirror of the Pydantic
// contract (apps/web/src/contract/schema.json). These TS types describe the
// server-authoritative shapes the renderer consumes; they are DATA shapes only
// — this file imports NO GhostCrawl SDK and NO Python package. When the authed
// WS lands its envelopes carry exactly these shapes into the SAME
// Zustand store.

/** A pixel/tile coordinate (contract: Point). */
export interface Point {
  x: number;
  y: number;
}

/**
 * An 8-way compass facing (contract: the `face` command's additive `facing` arg).
 * Byte-parity with the client `facingFromVector` / server `_FACING_SECTOR`
 * buckets — the SAME string set selects the exact clip the renderer would derive
 * from a movement vector (no new sprite art). Defined ONCE here; consumers
 * consume, they do not redefine. Mirrors `@ghostopia/ghost-art`'s `Facing8`.
 */
export type Facing = "s" | "se" | "e" | "ne" | "n" | "nw" | "w" | "sw";

/**
 * The kind of work a ghost performs (contract: the server `browser.view` mode
 * envelope). `browser-nav` drives a live browser view; `api-only`
 * drives an activity/data view instead of an (empty) browser frame. Additive-
 * optional; absent → treat as the graceful `browser-nav` default.
 */
export type GhostWorkKind = "browser-nav" | "api-only";

/**
 * The authoritative coarse lifecycle FSM (contract: GhostState). Server-owned;
 * the client only interpolates/renders it. Mirrors schema.json $defs.GhostState.
 */
export type GhostState =
  | "IDLE"
  | "RECEIVING_TASK"
  | "WALKING"
  | "AT_WORKSTATION"
  | "OPENING_BROWSER"
  | "NAVIGATING"
  | "SEARCHING"
  | "READING"
  | "SCROLLING"
  | "EXTRACTING"
  | "PROCESSING"
  | "WAITING"
  | "RETRYING"
  | "ERROR"
  | "COMPLETED"
  | "RETURNING_HOME";

/**
 * Whether a ghost needs the OPERATOR (contract: GhostAttention). Set when a ghost
 * hits a state only an operator can clear (captcha / non-retryable error / task.failed / pool-
 * exhausted); cleared on resolution. `reason` is a short server-sourced code.
 */
export interface GhostAttention {
  needs: boolean;
  reason?: string | null;
}

/**
 * A visual worker entity (contract: Ghost). Differentiated by name/section/task/
 * location/UI — NEVER by RPG class. `state` defaults to IDLE and
 * `position` is null until the server places it.
 */
export interface Ghost {
  id: string;
  name: string;
  /**
   * VESTIGIAL: graves are transient shared rest spots chosen
   * nearest-free, never a designated per-ghost home — the server no longer writes
   * a `home_grave` on spawn. Kept OPTIONAL because legacy envelopes / seeds may
   * still carry it and the client defaults it to `""`; do not read it for home
   * selection (that is server-authoritative). Marked optional, not removed.
   */
  home_grave?: string;
  position?: Point | null;
  section?: string | null;
  state: GhostState;
  task_id?: string | null;
  behavior_override?: string | null;
  /** operator-attention flag from the server status envelope; absent → no alert. */
  attention?: GhostAttention | null;
  /** the ghost's current task/mission subject (name-tag source, filler-stripped by the renderer). */
  subject?: string | null;
  /**
   * The explicit server-authored 8-way facing. Threaded off the `face`
   * command so a STATIONARY working ghost visibly orients to its workstation
   * (instead of sticking at its last movement-delta direction). Additive-optional
   * — when absent the renderer falls back to the movement-derived facing.
   */
  facing?: Facing | null;
  /**
   * The ghost's work kind (from the server `browser.view` mode
   * envelope). Drives the inspector's browser-vs-activity view branch. Additive-
   * optional; absent → the graceful `browser-nav` default.
   */
  workKind?: GhostWorkKind | null;
  /**
   * A customer-safe, server-SANITIZED persona sentence — e.g.
   * "browsing as a Chrome-class desktop from a US locale". Whitelist-built +
   * banned-lexicon-gated upstream; the client renders the safe string only, or
   * omits when absent (never a raw UA / engine codename). Additive-optional.
   */
  persona?: string | null;
  /**
   * Optional ARBITRARY body colour (0xRRGGBB). When set, the renderer recolors
   * the ONE ghost family's body ramp to this hue and plays the IDENTICAL shared
   * animation clips (idle/move/work/success/error). Takes precedence over
   * `section` for colour. `null`/undefined -> use the section palette (or base).
   */
  color?: number | null;
}

/**
 * A transient speech/thought BUBBLE above a ghost (contract: a `ghost.command` say/overlay).
 * The text/icon comes ONLY from a server envelope (never client-authored copy). The renderer
 * draws a small original pixel bubble that fades after `ttlMs`; `createdMs` lets it detect a
 * replacement (a newer bubble for the same ghost). One active bubble per ghost.
 */
export interface Bubble {
  ghostId: string;
  /** the server-provided line (say) or overlay label. */
  text: string;
  /** `say` = speech bubble (pointed tail), `overlay` = thought bubble (dotted tail). */
  kind: "say" | "overlay";
  /** wall-clock ms the bubble was pushed (identity for replacement detection). */
  createdMs: number;
  /** lifetime in ms before it fully fades out. */
  ttlMs: number;
}

/**
 * An autonomous graveyard CRITTER (contract: a `critter.spawned` / `critter.update`
 * envelope). Server-authoritative: the pure ghost-world FSM steps it and the
 * server broadcasts its pixel position + coarse state; the renderer only draws it. Original
 * graveyard idiom — a black `cat` (ground layer) + a `wisp` / `bat` (overhead layer).
 */
export interface Critter {
  id: string;
  /** `"cat"` (ground) | `"wisp"` | `"bat"` (overhead). */
  kind: "cat" | "wisp" | "bat";
  /** world-pixel position. */
  x: number;
  y: number;
  /** FSM state — `"idle"` | `"wander"` | `"follow"`. */
  state: "idle" | "wander" | "follow";
  /** horizontal travel sign (−1/0/+1) — the renderer flips the ground cat by this. */
  facing: number;
  /** `"ground"` (depth-sorted with ghosts) | `"overhead"`. */
  layer: "ground" | "overhead";
}

/**
 * A `ghost.status_changed`-shaped update — the wire shape the server envelope
 * carries. Carries `ghost_id` (the envelope key) + the changed fields. Applied
 * into the store via `applyStatusChanged`, which merges by id.
 */
export interface GhostStatusChanged {
  ghost_id: string;
  state?: GhostState;
  section?: string | null;
  position?: Point | null;
  task_id?: string | null;
  /** additive explicit facing — the `face` command threads it through this envelope. */
  facing?: Facing | null;
}
