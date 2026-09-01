// ghostopia ghost-renderer — spawn/dissolve flourish.
//
// An ORIGINAL "fog-condense" materialize + a "spectral scatter" dissolve for ghosts —
// NOT a matrix-rain column. On ghost.spawned a ring of soft mist motes condenses INWARD
// as the ghost fades in; on despawn the motes scatter OUTWARD as it fades out. Reduced
// motion collapses either to a quick alpha fade (the caller checks the flag).
//
// The pure particle math lives here (unit-tested); the PixiJS particle container is a thin
// builder around it. Imports only PixiJS.

import { Container, Graphics } from "pixi.js";

import { hash2 } from "./visuals.js";

/** The two flourish directions. */
export type FlourishKind = "materialize" | "dissolve";

/** Full flourish duration (ms). */
export const FLOURISH_MS = 460;

/** A single seeded mote on the flourish ring: a stable angle + max radius + size. */
export interface FlourishParticle {
  angle: number;
  radius: number;
  size: number;
}

/** A sampled mote at a normalized time `t` (0..1): world offset + alpha + scale. */
export interface ParticleSample {
  x: number;
  y: number;
  alpha: number;
  scale: number;
}

/** Ease-out cubic — snappy at the start, settling at the end (both directions read punchy). */
export function easeOutCubic(t: number): number {
  const c = 1 - t;
  return 1 - c * c * c;
}

/**
 * Deterministically seed `count` motes around a ring (stable per `seed`, so a re-render of the
 * same ghost looks identical). Pure — no PixiJS.
 */
export function seedFlourishParticles(count: number, seed: number): FlourishParticle[] {
  const out: FlourishParticle[] = [];
  const n = Math.max(1, Math.floor(count));
  for (let i = 0; i < n; i++) {
    const jitter = (hash2(i, 1, seed) - 0.5) * 0.6;
    const angle = (i / n) * Math.PI * 2 + jitter;
    const radius = 10 + hash2(i, 2, seed) * 12;
    const size = 1.2 + hash2(i, 3, seed) * 1.8;
    out.push({ angle, radius, size });
  }
  return out;
}

/**
 * Sample a mote at normalized time `t`. For `materialize` the mote starts at its full radius and
 * condenses to the centre while fading out (the ghost solidifying); for `dissolve` it starts at
 * the centre and scatters to full radius while fading out. Pure — unit-tested.
 */
export function sampleParticle(p: FlourishParticle, kind: FlourishKind, t: number): ParticleSample {
  const tc = t < 0 ? 0 : t > 1 ? 1 : t;
  const e = easeOutCubic(tc);
  // radius fraction: materialize 1→0 (converge), dissolve 0→1 (scatter)
  const rf = kind === "materialize" ? 1 - e : e;
  const r = p.radius * rf;
  return {
    x: Math.cos(p.angle) * r,
    y: Math.sin(p.angle) * r,
    // motes fade out as the flourish completes (ghost alpha is the inverse — see ghostAlphaFor)
    alpha: (1 - tc) * 0.85,
    scale: kind === "materialize" ? 0.6 + (1 - tc) * 0.6 : 0.6 + tc * 0.6,
  };
}

/**
 * The GHOST body alpha during a flourish: `materialize` fades the ghost IN (0→1), `dissolve`
 * fades it OUT (1→0). Pure.
 */
export function ghostAlphaFor(kind: FlourishKind, t: number): number {
  const tc = t < 0 ? 0 : t > 1 ? 1 : t;
  const e = easeOutCubic(tc);
  return kind === "materialize" ? e : 1 - e;
}

/** A running PixiJS flourish effect: a mote container + a per-frame `update` returning `done`. */
export interface FlourishEffect {
  container: Container;
  /** advance by a clamped dt (ms); returns true once complete (caller destroys it). */
  update: (dt: number) => boolean;
}

/**
 * Build a flourish mote container at world origin (position it at the ghost). `seed` keeps the
 * mote ring stable per ghost; `tint` is the ghost's spectral colour. The caller adds
 * `effect.container` to an overhead layer, positions it, and calls `update(dt)` until it returns
 * true. Original fog motes — never a matrix-rain column.
 */
export function makeFlourish(kind: FlourishKind, seed: number, tint = 0xbcc6ea): FlourishEffect {
  const container = new Container();
  container.label = `flourish.${kind}`;
  container.eventMode = "none";
  const particles = seedFlourishParticles(12, seed);
  const dots = particles.map((p) => {
    const g = new Graphics().circle(0, 0, p.size).fill({ color: tint, alpha: 0.85 });
    g.blendMode = "add";
    container.addChild(g);
    return g;
  });
  let t = 0;
  const update = (dt: number): boolean => {
    t += dt / FLOURISH_MS;
    for (let i = 0; i < dots.length; i++) {
      const s = sampleParticle(particles[i], kind, t);
      dots[i].position.set(s.x, s.y);
      dots[i].alpha = s.alpha;
      dots[i].scale.set(s.scale);
    }
    return t >= 1;
  };
  return { container, update };
}
