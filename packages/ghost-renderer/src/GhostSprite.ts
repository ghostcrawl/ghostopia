// ghostopia ghost-renderer — the ONE cute-spooky ghost, directional + animated,
// ANY colour.
//
// Drives the art runtime contract (docs/ART_RUNTIME_CONTRACT.md):
//   1. TURN from the movement vector via `facingFromVector` (8-way, honouring
//      the mirror flag with a horizontal flip).
//   2. PICK the clip for the ghost's state (idle.<facing> / move.<facing> /
//      work / success / error).
//   3. PLAY the clip on a ms clock (per-frame `ms` from animations.json).
//   4. MIRROR west facings by flipping the sprite horizontally.
//   5. COLOUR by recolor: the frame set is resolved per-ghost by a provider
//      (arbitrary hue OR a named section) — the clips + facing are IDENTICAL for
//      every colour. A neutral status tint (brightness) rides on top so success
//      brightens / error darkens WITHOUT changing the hue.
// A soft elliptical drop-shadow grounds the ghost; a float-bob y-offset sine adds
// life while stationary.
//
// Imports only PixiJS + the pure helpers/types from @ghostopia/ghost-art.

import { Container, Graphics, Sprite, type Texture } from "pixi.js";
import {
  facingFromVector,
  MIRROR_SOURCE,
  type AnimationBook,
  type Clip,
  type Facing8,
  type FacingResult,
} from "@ghostopia/ghost-art";

import type { Facing, Ghost, GhostState } from "./contract.js";
import {
  drawSelectionOutline,
  fillerStrip,
  identityBadge,
  makeActionGlyph,
  makeAlertMarker,
  makeNameTag,
  makeWaitingDots,
  makeZzz,
  type ActionGlyphKind,
  type IdentityBadge,
} from "./overlays.js";

/** Per-frame UI flags the render loop passes for the ghost's overlays. */
export interface GhostSpriteUi {
  /** this ghost is the selected one (draws the selection outline). */
  selected?: boolean;
  /** show the name tag even when not selected ("always show labels" toggle). */
  showLabel?: boolean;
  /** prefers-reduced-motion: freeze the alert pulse / waiting bob to a steady state. */
  reduceMotion?: boolean;
  /**
   * Grave-rest (196): while the ghost is sinking into / sunk in its home gravestone (state IDLE)
   * the render loop sets this so the stationary float-bob is suppressed — a resting ghost reads
   * as "gone into the grave", never hovering above it.
   */
  suppressBob?: boolean;
}

/** The ghost-states that mean "busy at the crypt-terminal" -> the front `work` clip. */
const WORK_STATES = new Set<GhostState>([
  "AT_WORKSTATION",
  "OPENING_BROWSER",
  "NAVIGATING",
  "SEARCHING",
  "READING",
  "SCROLLING",
  "EXTRACTING",
  "PROCESSING",
  "WAITING",
  "RETRYING",
]);

/**
 * Per-kind work clips: a ghost's specific browser work phase → an
 * `work.<kind>` clip so an observer can tell WHAT it is doing at the workstation.
 * States without a distinct phase (opening / at-station / processing / waiting)
 * fall back to the generic `work` clip.
 */
export const WORK_STATE_CLIP: Partial<Record<GhostState, string>> = {
  NAVIGATING: "work.navigating",
  SEARCHING: "work.searching",
  READING: "work.reading",
  SCROLLING: "work.scrolling",
  EXTRACTING: "work.extracting",
};

/** The action-glyph kind (drawn above the ghost) for a work state, or null for none. */
export function actionKindForState(state: GhostState): string | null {
  switch (state) {
    case "NAVIGATING":
      return "navigating";
    case "SEARCHING":
      return "searching";
    case "READING":
      return "reading";
    case "SCROLLING":
      return "scrolling";
    case "EXTRACTING":
      return "extracting";
    default:
      return null;
  }
}

/**
 * Resolve the ghost's EFFECTIVE facing for this frame.
 *
 * While MOVING the facing follows the movement vector (the walk cycle turns the
 * ghost along its A* heading). While STATIONARY, honor the explicit server-authored
 * `facing` when present — so a working ghost faces its workstation and an idle ghost
 * settles toward a sensible direction, instead of sticking at the dx=dy=0 movement
 * delta (which always collapses to the south idle default). `mirror` is recomputed
 * for the explicit facing from `MIRROR_SOURCE` (west set sw/w/nw flips the authored
 * east art) — byte-parity with `facingFromVector`, no new sprite clips.
 */
export function resolveFacing(
  moving: boolean,
  explicit: Facing | null | undefined,
  movement: FacingResult,
): FacingResult {
  if (!moving && explicit) {
    const facing = explicit as Facing8;
    return { facing, mirror: MIRROR_SOURCE[facing] !== facing };
  }
  return movement;
}

/**
 * Whether the Zzz "resting" overlay should show. Reserved for a
 * TRUE grave-rest state (`IDLE`) ONLY — never a between-walk pause (which stays
 * `WALKING`/`RETURNING_HOME` while the server chains the next waypoint) nor a working
 * state. Extracted as a pure predicate so the "reserved for true rest" contract is
 * testable without PixiJS.
 */
export function restingZzzVisible(state: GhostState): boolean {
  return state === "IDLE";
}

/** Map a ghost's runtime state (+ facing/movement) to a clip name (contract §2). */
export function clipNameForGhost(state: GhostState, facing: string, moving: boolean): string {
  if (moving || state === "WALKING") return `move.${facing}`;
  if (state === "COMPLETED") return "success";
  if (state === "ERROR") return "error";
  if (WORK_STATE_CLIP[state]) return WORK_STATE_CLIP[state] as string;
  if (WORK_STATES.has(state)) return "work";
  return `idle.${facing}`;
}

/** Per-status brightness applied as a NEUTRAL tint (hue comes from the recolor). */
function statusFactor(state: GhostState): number {
  if (state === "ERROR") return 0.72;
  if (state === "RETRYING") return 0.86;
  if (state === "COMPLETED") return 1.18;
  if (WORK_STATES.has(state) || state === "WALKING") return 1.05;
  return 1.0;
}

/** Multiply a 0xRRGGBB colour by a scalar factor, clamped per channel. */
export function scaleColor(color: number, factor: number): number {
  const r = Math.min(255, Math.round(((color >> 16) & 0xff) * factor));
  const g = Math.min(255, Math.round(((color >> 8) & 0xff) * factor));
  const b = Math.min(255, Math.round((color & 0xff) * factor));
  return (r << 16) | (g << 8) | b;
}

/** Resolve the (cached) recolored frame set for a ghost's colour/section. */
export type FrameResolver = (ghost: Ghost) => Record<string, Texture>;

export interface GhostSpriteOptions {
  /** returns the recolored "ghost:region" -> Texture set for this ghost. */
  resolveFrames: FrameResolver;
  /** the clip table (from animations.json). */
  book: AnimationBook;
  /** idle float-bob amplitude in world pixels (default 1.5). */
  bobAmplitude?: number;
  /** idle float-bob period in ms (default 1400). */
  bobPeriodMs?: number;
}

/**
 * One rendered ghost. Owns a Pixi `Container` (positioned at the ghost's world
 * pixel coords) holding a soft ground-shadow + a bottom-anchored `Sprite`.
 * `update(ghost, dtMs)` turns, clips, plays, mirrors, colours, and bobs it.
 */
export class GhostSprite {
  readonly container: Container;
  private readonly sprite: Sprite;
  private readonly shadow: Graphics;
  private readonly resolveFrames: FrameResolver;
  private readonly book: AnimationBook;
  private readonly bobAmplitude: number;
  private readonly bobPeriodMs: number;

  private frames: Record<string, Texture> = {};
  private clipName = "idle.s";
  private frameIdx = 0;
  private frameAcc = 0;
  private bobT = 0;
  private lastX: number | null = null;
  private lastY: number | null = null;
  private shadowW = 0;
  private spriteH = 0;

  // ---- liveness overlays — all children of `overlayLayer`, above the sprite ----
  private readonly overlayLayer: Container;
  private readonly selectionG: Graphics;
  private readonly alert: Container;
  private readonly zzz: Container;
  private readonly waiting: { container: Container; update: (t: number) => void };
  private actionGlyph: Container | null = null;
  private actionGlyphKind: string | null = null;
  private nameTag: Container | null = null;
  private nameTagLabel = "";
  private badge: IdentityBadge | null = null;
  private overlayT = 0;

  constructor(options: GhostSpriteOptions) {
    this.resolveFrames = options.resolveFrames;
    this.book = options.book;
    this.bobAmplitude = options.bobAmplitude ?? 1.5;
    this.bobPeriodMs = options.bobPeriodMs ?? 1400;

    this.container = new Container();

    // selection outline sits UNDER the sprite (a ring around the footprint).
    this.selectionG = new Graphics();
    this.selectionG.visible = false;
    this.container.addChild(this.selectionG);

    // soft elliptical ground shadow (drawn once; sized on the first frame)
    this.shadow = new Graphics();
    this.container.addChild(this.shadow);

    this.sprite = new Sprite();
    this.sprite.anchor.set(0.5, 1); // bottom-centre: sits on the ground, bobs up
    this.container.addChild(this.sprite);

    // the overlay layer floats ABOVE the head; it bobs with the sprite but never mirror-flips.
    this.overlayLayer = new Container();
    this.overlayLayer.eventMode = "none";
    this.alert = makeAlertMarker();
    this.zzz = makeZzz();
    this.waiting = makeWaitingDots();
    this.alert.visible = false;
    this.zzz.visible = false;
    this.waiting.container.visible = false;
    this.overlayLayer.addChild(this.zzz, this.waiting.container, this.alert);
    this.container.addChild(this.overlayLayer);
  }

  private clip(): Clip | undefined {
    return this.book[this.clipName] ?? this.book["idle.s"];
  }

  private currentFrameTexture(): Texture | undefined {
    const clip = this.clip();
    if (!clip) return undefined;
    const f = clip.frames[Math.min(this.frameIdx, clip.frames.length - 1)];
    return this.frames[`${f.sprite}:${f.region}`];
  }

  private setFrame(tex: Texture | undefined): void {
    if (tex && this.sprite.texture !== tex) {
      this.sprite.texture = tex;
      this.spriteH = tex.height;
      // size the shadow to the sprite width once we know a real frame size
      if (tex.width !== this.shadowW) {
        this.shadowW = tex.width;
        this.shadow.clear();
        const rx = Math.max(4, tex.width * 0.42);
        const ry = Math.max(1.5, tex.width * 0.16);
        this.shadow.ellipse(0, -1, rx, ry).fill({ color: 0x0a0812, alpha: 0.28 });
      }
    }
  }

  /** Advance + render this ghost for a clamped frame delta (ms). */
  update(ghost: Ghost, dtMs: number, ui: GhostSpriteUi = {}): void {
    // resolve this ghost's (cached) recolored frame set; swap on a colour change
    const frames = this.resolveFrames(ghost);
    if (frames !== this.frames) {
      this.frames = frames;
      this.setFrame(this.currentFrameTexture());
    }

    // ---- 1. position + velocity (screen space: +dy = down = south) ----
    const px = ghost.position?.x ?? this.lastX ?? 0;
    const py = ghost.position?.y ?? this.lastY ?? 0;
    const dx = this.lastX === null ? 0 : px - this.lastX;
    const dy = this.lastY === null ? 0 : py - this.lastY;
    this.lastX = px;
    this.lastY = py;
    this.container.position.set(px, py);

    const moving = ghost.state === "WALKING" || Math.hypot(dx, dy) > 0.01;
    // honor the explicit server facing while stationary — otherwise the
    // idle/working ghost sticks at the collapsed dx=dy=0 south default.
    const { facing, mirror } = resolveFacing(moving, ghost.facing, facingFromVector(dx, dy));

    // ---- 2. pick the clip; reset the frame cursor on a clip change ----
    const nextClip = clipNameForGhost(ghost.state, facing, moving);
    if (nextClip !== this.clipName) {
      this.clipName = nextClip;
      this.frameIdx = 0;
      this.frameAcc = 0;
    }
    const clip = this.clip();
    if (!clip) return;

    // ---- 3. play on the ms clock (per-frame ms is DATA) ----
    this.frameAcc += dtMs;
    let safety = 0;
    while (this.frameAcc >= clip.frames[this.frameIdx].ms && safety < 64) {
      this.frameAcc -= clip.frames[this.frameIdx].ms;
      this.frameIdx += 1;
      if (this.frameIdx >= clip.frames.length) {
        this.frameIdx = clip.loop ? 0 : clip.frames.length - 1;
        if (!clip.loop) break;
      }
      safety += 1;
    }
    this.setFrame(this.currentFrameTexture());

    // ---- 4. mirror the west facings (or a mirror-flagged clip) ----
    const flip = mirror || clip.mirror;
    this.sprite.scale.x = flip ? -1 : 1;

    // ---- 5. neutral status brightness (hue already baked by the recolor) ----
    this.sprite.tint = scaleColor(0xffffff, statusFactor(ghost.state));

    // ---- float-bob while stationary (walk cycle carries its own motion) ----
    // Reduced-motion: ghosts still MOVE (position updates flow through the
    // container above), but the DECORATIVE idle float-bob is skipped.
    if (!moving && ui.reduceMotion !== true && ui.suppressBob !== true) {
      this.bobT = (this.bobT + dtMs) % this.bobPeriodMs;
      const phase = (this.bobT / this.bobPeriodMs) * Math.PI * 2;
      const lift = -Math.sin(phase) * this.bobAmplitude;
      this.sprite.y = lift;
      // shadow shrinks slightly as the ghost rises (subtle grounding cue)
      const s = 1 - Math.max(0, -lift) / (this.bobAmplitude * 8);
      this.shadow.scale.set(s, s);
    } else {
      this.sprite.y = 0;
      this.shadow.scale.set(1, 1);
    }

    // ---- liveness overlays: float them above the head, bobbing with the sprite ----
    this.updateOverlays(ghost, dtMs, ui);
  }

  /** Draw/refresh the per-ghost overlays (alert / zzz / working-dots / name tag / outline). */
  private updateOverlays(ghost: Ghost, dtMs: number, ui: GhostSpriteUi): void {
    this.overlayT += dtMs;
    const reduce = ui.reduceMotion === true;
    // the overlay stack floats with the sprite's bob, a few px above the head.
    const headY = -this.spriteH - 6;
    this.overlayLayer.y = this.sprite.y;

    // selection outline (under the sprite, at the footprint)
    if (ui.selected) {
      const tex = this.sprite.texture;
      drawSelectionOutline(this.selectionG, tex?.width ?? 0, tex?.height ?? 0);
      this.selectionG.visible = true;
    } else if (this.selectionG.visible) {
      this.selectionG.clear();
      this.selectionG.visible = false;
    }

    // attention "!" — a pulsing spectral marker when the ghost needs the operator
    if (ghost.attention?.needs) {
      this.alert.visible = true;
      this.alert.position.set(0, headY - 12);
      if (reduce) {
        this.alert.scale.set(1);
        this.alert.alpha = 1;
      } else {
        const p = 0.5 + 0.5 * Math.sin(this.overlayT * 0.006);
        this.alert.scale.set(0.9 + p * 0.25);
        this.alert.alpha = 0.75 + p * 0.25;
      }
    } else {
      this.alert.visible = false;
    }

    // Zzz over a TRUE grave-rest ghost only — never a
    // between-walk pause (which stays WALKING while the server chains waypoints).
    const idle = restingZzzVisible(ghost.state);
    if (idle) {
      this.zzz.visible = true;
      const lift = reduce ? 0 : Math.sin(this.overlayT * 0.003) * 2;
      this.zzz.position.set(3, headY - 6 + lift);
    } else {
      this.zzz.visible = false;
    }

    // working ellipsis "· · ·" — a ghost has no health/budget bar; instead, while it is busy
    // at the crypt-terminal (any WORK_STATES phase: opening / navigating / searching / reading /
    // scrolling / extracting / processing / waiting / retrying) it shows a soft drifting-dots
    // bubble so an observer can tell it is actively working (196 — ghost-appropriate, replaces
    // the removed fuel-gauge). A resting/walking ghost shows nothing.
    const working = WORK_STATES.has(ghost.state);
    if (working) {
      this.waiting.container.visible = true;
      this.waiting.container.position.set(0, headY - 6);
      this.waiting.update(reduce ? 0 : this.overlayT);
    } else {
      this.waiting.container.visible = false;
    }

    // action glyph (P8): a small spectral mark beside the head telling WHAT the ghost is doing
    // at the workstation (navigating / searching / reading / scrolling / extracting).
    const actionKind = actionKindForState(ghost.state);
    if (actionKind) {
      if (this.actionGlyphKind !== actionKind || !this.actionGlyph) {
        if (this.actionGlyph) this.actionGlyph.destroy();
        this.actionGlyph = makeActionGlyph(actionKind as ActionGlyphKind);
        this.actionGlyphKind = actionKind;
        this.overlayLayer.addChild(this.actionGlyph);
      }
      const glyph = this.actionGlyph;
      const bob = reduce ? 0 : Math.sin(this.overlayT * 0.005) * 1.5;
      glyph.position.set(12, headY + 2 + bob);
      glyph.visible = true;
    } else if (this.actionGlyph) {
      this.actionGlyph.visible = false;
    }

    // name tag + identity badge (when selected or "always show labels")
    if (ui.selected || ui.showLabel) {
      const raw = ghost.subject ?? ghost.name ?? "";
      const label = fillerStrip(raw) || ghost.name || ghost.id;
      if (!this.badge) this.badge = identityBadge(ghost.id);
      if (!this.nameTag || label !== this.nameTagLabel) {
        if (this.nameTag) this.nameTag.destroy();
        this.nameTag = makeNameTag(label, this.badge);
        this.nameTagLabel = label;
        this.container.addChild(this.nameTag);
      }
      // sit the tag just above the head (no health bar to clear anymore)
      this.nameTag.position.set(0, headY - 8 + this.sprite.y);
      this.nameTag.visible = true;
    } else if (this.nameTag) {
      this.nameTag.visible = false;
    }
  }

  /**
   * The ghost's world-space footprint for pointer hit-testing (bottom-centre anchored), or
   * `null` before a real frame is sized. Ignores the idle float-bob (a stable click target).
   */
  hitBox(): { x: number; y: number; w: number; h: number } | null {
    const tex = this.sprite.texture;
    const w = tex?.width ?? 0;
    const h = tex?.height ?? 0;
    if (w < 2 || h < 2) return null; // no real frame resolved yet
    const cx = this.container.x;
    const cy = this.container.y; // ground point (anchor 0.5,1)
    return { x: cx - w / 2, y: cy - h, w, h };
  }

  /** Detach + free this ghost's display objects. */
  destroy(): void {
    this.container.destroy({ children: true });
  }
}
