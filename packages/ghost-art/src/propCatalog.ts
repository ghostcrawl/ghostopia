// ghostopia ghost-art — the data-driven PLACEABLE-PROP CATALOG.
//
// A prop is DATA: an id + category, the manifest sprite (grid) it draws from, a tile
// FOOTPRINT (how many tiles it occupies — fed into server collision + A*), an ORIENTATION
// set (rotation 1/2/4-way; a mirror flag reuses one region for the flipped facing), an
// optional on/off STATE region pair (lantern lit/dark), an optional animation CLIP, and a
// PLACEMENT rule (on-ground / against-fence / against-wall). A placed prop is then fully
// described by {catalog_id, tile, orientation, state} — nothing about a prop is hard-coded
// in the renderer, so the SAME catalog powers both the shipped world AND the in-app editor.
//
// `loadPropCatalog(json, manifest, book?)` VALIDATES every grid/region ref against the
// manifest and every anim clip against the animation book (when supplied) — a missing ref
// throws loudly, naming the offender, so an authoring typo can never ship an invisible prop.

import type { SpriteManifest } from "./manifest.js";
import type { AnimationBook } from "./animations.js";

/** How many TILES a prop occupies on the ground (fed into server collision + A*). */
export interface Footprint {
  w: number;
  h: number;
}

/** Where a prop may be placed (the in-app editor uses this to gate placement). */
export type Placement = "ground" | "fence" | "wall";

/**
 * One orientation of a prop. Either it names a manifest `region` directly, OR it `mirror`s
 * another orientation (draw that orientation's region horizontally flipped) — so a 2-way /
 * 4-way facing set needs only the distinct art, the rest are cheap mirrors.
 */
export interface PropOrientation {
  region?: string;
  mirror?: string;
}

/** A validated prop definition (manifest cell size resolved in for the renderer). */
export interface PropDef {
  id: string;
  category: string;
  /** the manifest sprite (grid) this prop draws from. */
  sprite: string;
  /** cell size resolved from the manifest sprite (renderer sizing / footprint sanity). */
  cellWidth: number;
  cellHeight: number;
  footprint: Footprint;
  placement: Placement;
  /** orientation name -> region/mirror resolution. */
  orientations: Record<string, PropOrientation>;
  defaultOrientation: string;
  /** optional on/off (or arbitrary) state -> region map (e.g. lantern off/on). */
  states?: Record<string, string>;
  defaultState?: string;
  /** optional animation clip name (validated against the animation book when supplied). */
  anim?: string;
}

/** The validated catalog: id -> PropDef. */
export interface PropCatalog {
  props: Record<string, PropDef>;
}

const PLACEMENTS: ReadonlySet<string> = new Set<Placement>(["ground", "fence", "wall"]);

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asFootprint(v: unknown, where: string): Footprint {
  if (!isObject(v)) throw new Error(`prop ${where}: footprint must be an object`);
  const w = v.w;
  const h = v.h;
  if (typeof w !== "number" || typeof h !== "number" || w < 1 || h < 1) {
    throw new Error(`prop ${where}: footprint needs integer w>=1, h>=1`);
  }
  return { w: Math.trunc(w), h: Math.trunc(h) };
}

/**
 * Parse + validate a prop catalog JSON.
 * - every prop's `sprite` MUST exist in `manifest.sprites` (else throw);
 * - every orientation `region` MUST exist in that sprite's regions (else throw);
 * - a `mirror` orientation MUST name another declared orientation (else throw);
 * - `defaultOrientation` MUST be one of the declared orientations (else throw);
 * - every `states` value MUST be a region on the sprite; `defaultState` (if any) a declared state;
 * - `placement` MUST be ground|fence|wall;
 * - when `book` is supplied, an `anim` MUST name a clip in it (else throw).
 */
export function loadPropCatalog(
  json: unknown,
  manifest: SpriteManifest,
  book?: AnimationBook,
): PropCatalog {
  if (!isObject(json) || !isObject(json.props)) {
    throw new Error('invalid prop catalog: expected an object with a "props" map');
  }
  const props: Record<string, PropDef> = {};
  for (const [id, raw] of Object.entries(json.props)) {
    if (!isObject(raw)) throw new Error(`invalid prop entry "${id}"`);

    const sprite = raw.sprite;
    if (typeof sprite !== "string") throw new Error(`prop "${id}" missing string "sprite"`);
    const spriteEntry = manifest.sprites[sprite];
    if (!spriteEntry) {
      throw new Error(`prop "${id}" references missing sprite "${sprite}"`);
    }
    const regionExists = (r: string): boolean => Object.hasOwn(spriteEntry.regions, r);

    const category = typeof raw.category === "string" ? raw.category : "prop";

    const footprint = asFootprint(raw.footprint, `"${id}"`);

    const placement = raw.placement;
    if (typeof placement !== "string" || !PLACEMENTS.has(placement)) {
      throw new Error(
        `prop "${id}" has invalid placement "${String(placement)}" (want ground|fence|wall)`,
      );
    }

    if (!isObject(raw.orientations) || Object.keys(raw.orientations).length === 0) {
      throw new Error(`prop "${id}" needs a non-empty "orientations" map`);
    }
    const orientations: Record<string, PropOrientation> = {};
    // pass 1: collect + validate region-bearing orientations
    for (const [oname, ov] of Object.entries(raw.orientations)) {
      if (!isObject(ov)) throw new Error(`prop "${id}" orientation "${oname}" is not an object`);
      const region = typeof ov.region === "string" ? ov.region : undefined;
      const mirror = typeof ov.mirror === "string" ? ov.mirror : undefined;
      if (!region && !mirror) {
        throw new Error(`prop "${id}" orientation "${oname}" needs a "region" or a "mirror"`);
      }
      if (region && !regionExists(region)) {
        throw new Error(
          `prop "${id}" orientation "${oname}" references missing region "${sprite}:${region}"`,
        );
      }
      orientations[oname] = { region, mirror };
    }
    // pass 2: validate mirror targets exist AND resolve to a real region
    for (const [oname, o] of Object.entries(orientations)) {
      if (o.mirror) {
        const target = orientations[o.mirror];
        if (!target) {
          throw new Error(
            `prop "${id}" orientation "${oname}" mirrors unknown orientation "${o.mirror}"`,
          );
        }
        if (!target.region) {
          throw new Error(
            `prop "${id}" orientation "${oname}" mirrors "${o.mirror}" which has no region`,
          );
        }
      }
    }

    const defaultOrientation = raw.defaultOrientation;
    if (typeof defaultOrientation !== "string" || !orientations[defaultOrientation]) {
      throw new Error(
        `prop "${id}" defaultOrientation "${String(defaultOrientation)}" is not a declared orientation`,
      );
    }

    let states: Record<string, string> | undefined;
    let defaultState: string | undefined;
    if (raw.states !== undefined) {
      if (!isObject(raw.states)) throw new Error(`prop "${id}" "states" must be an object`);
      states = {};
      for (const [sname, sregion] of Object.entries(raw.states)) {
        if (typeof sregion !== "string" || !regionExists(sregion)) {
          throw new Error(
            `prop "${id}" state "${sname}" references missing region "${sprite}:${String(sregion)}"`,
          );
        }
        states[sname] = sregion;
      }
      if (raw.defaultState !== undefined) {
        if (typeof raw.defaultState !== "string" || !states[raw.defaultState]) {
          throw new Error(
            `prop "${id}" defaultState "${String(raw.defaultState)}" is not a declared state`,
          );
        }
        defaultState = raw.defaultState;
      }
    }

    let anim: string | undefined;
    if (raw.anim !== undefined) {
      if (typeof raw.anim !== "string") throw new Error(`prop "${id}" "anim" must be a string`);
      if (book && !Object.hasOwn(book, raw.anim)) {
        throw new Error(`prop "${id}" references missing anim clip "${raw.anim}"`);
      }
      anim = raw.anim;
    }

    props[id] = {
      id,
      category,
      sprite,
      cellWidth: spriteEntry.cellWidth,
      cellHeight: spriteEntry.cellHeight,
      footprint,
      placement: placement as Placement,
      orientations,
      defaultOrientation,
      states,
      defaultState,
      anim,
    };
  }
  return { props };
}

/** The manifest region + mirror flag a placed prop draws for a given orientation + state. */
export interface ResolvedPropRegion {
  region: string;
  mirror: boolean;
}

/**
 * Resolve which manifest region (and mirror flag) to draw for a placed prop.
 *
 * A STATE (when the prop has states) overrides the region — a lantern's lit/dark art is a
 * distinct region regardless of facing. Otherwise the region comes from the ORIENTATION
 * (a `mirror` orientation resolves to its base orientation's region, drawn flipped). An
 * unknown orientation falls back to the prop's `defaultOrientation`; an unknown/omitted
 * state falls back to `defaultState`. This is a PURE function (unit-tested).
 */
export function resolvePropRegion(
  def: PropDef,
  orientation?: string,
  state?: string,
): ResolvedPropRegion {
  // orientation → base region + mirror
  const oname = orientation && def.orientations[orientation] ? orientation : def.defaultOrientation;
  const ori = def.orientations[oname];
  let region: string;
  let mirror = false;
  if (ori.mirror) {
    const base = def.orientations[ori.mirror];
    region = base.region as string;
    mirror = true;
  } else {
    region = ori.region as string;
  }
  // state overrides the region (lantern off/on etc.)
  if (def.states) {
    const sname = state && def.states[state] ? state : def.defaultState;
    if (sname && def.states[sname]) region = def.states[sname];
  }
  return { region, mirror };
}
