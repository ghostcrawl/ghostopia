// ghostopia ghost-art — the animation-clip layer.
//
// Sprites are DYNAMIC, not stills. A "clip" is an ordered list of frames, each a
// (sprite, region, durationMs), plus loop + optional mirror flags. The renderer
// plays a clip by accumulating a wall-clock ms delta and advancing frames — the
// timing is DATA, authored once here, never hard-coded in the art package.
//
// `loadAnimations(json, manifest)` is the SINGLE contract the renderer consumes.
// It VALIDATES every frame's (sprite, region) against the loaded manifest, so a
// typo can never ship an invisible ghost — a missing ref throws loudly, naming
// the offending "sprite:region".

import type { SpriteManifest } from "./manifest.js";

/** One played frame: a manifest region shown for `ms` milliseconds. */
export interface Frame {
  sprite: string;
  region: string;
  ms: number;
}

/** An ordered, timed clip. `mirror` = draw horizontally flipped (west facings). */
export interface Clip {
  frames: Frame[];
  loop: boolean;
  mirror: boolean;
}

/** clip name -> Clip. The renderer looks clips up by name (e.g. `idle.se`). */
export type AnimationBook = Record<string, Clip>;

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function regionExists(
  manifest: SpriteManifest,
  sprite: string,
  region: string,
): boolean {
  const entry = manifest.sprites[sprite];
  return !!entry && !!entry.regions[region];
}

/**
 * Parse + validate an animations book against a manifest.
 * Throws (loudly) on: a non-object book, a missing `clips` map, an empty frame
 * list, a non-positive `ms`, a non-string sprite/region, or ANY frame whose
 * (sprite, region) is absent from the manifest.
 */
export function loadAnimations(
  json: unknown,
  manifest: SpriteManifest,
): AnimationBook {
  if (!isObject(json) || !isObject(json.clips)) {
    throw new Error('invalid animations: expected an object with a "clips" map');
  }
  const book: AnimationBook = {};
  for (const [name, raw] of Object.entries(json.clips)) {
    if (!isObject(raw) || !Array.isArray(raw.frames)) {
      throw new Error(`clip "${name}" missing a "frames" array`);
    }
    if (raw.frames.length === 0) {
      throw new Error(`clip "${name}" has an empty frame list`);
    }
    const frames: Frame[] = [];
    for (let i = 0; i < raw.frames.length; i++) {
      const f = raw.frames[i] as unknown;
      if (!isObject(f)) throw new Error(`clip "${name}" frame ${i} is not an object`);
      const { sprite, region, ms } = f as Record<string, unknown>;
      if (typeof sprite !== "string" || typeof region !== "string") {
        throw new Error(`clip "${name}" frame ${i} missing string sprite/region`);
      }
      if (typeof ms !== "number" || !(ms > 0)) {
        throw new Error(
          `clip "${name}" frame ${i} (${sprite}:${region}) needs ms > 0`,
        );
      }
      if (!regionExists(manifest, sprite, region)) {
        throw new Error(
          `clip "${name}" frame ${i} references missing region "${sprite}:${region}"`,
        );
      }
      frames.push({ sprite, region, ms });
    }
    book[name] = {
      frames,
      loop: typeof raw.loop === "boolean" ? raw.loop : true,
      mirror: typeof raw.mirror === "boolean" ? raw.mirror : false,
    };
  }
  return book;
}
