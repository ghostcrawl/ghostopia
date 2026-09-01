// ghostopia ghost-art — data loaders for sprites.manifest.json + palettes.json.
//
// The manifest is the SINGLE source the atlas reads: name -> grid ref -> region
// frame table. Adding a sprite = drop a JSON grid + a manifest entry; no
// renderer/atlas code change. `loadManifest` validates structure and (given the
// set of available grid names) rejects a manifest whose grid ref is missing.

import {
  BASE_RAMP,
  DEFAULT_STATUS_TINTS,
  sectionPaletteFromRamps,
  type SectionPalette,
  type StatusTint,
} from "./palette.js";

export type { SectionPalette } from "./palette.js";

/** A rectangular region of a sprite's grid, in CELL units (col,row) × (cols,rows). */
export interface SpriteRegion {
  col: number;
  row: number;
  cols: number;
  rows: number;
}

/** One catalogued sprite: which grid it lives in, its cell size, and its frames. */
export interface SpriteEntry {
  grid: string;
  cellWidth: number;
  cellHeight: number;
  regions: Record<string, SpriteRegion>;
}

/** The data-driven sprite catalogue. */
export interface SpriteManifest {
  cellDefault?: { width: number; height: number };
  sprites: Record<string, SpriteEntry>;
}

/** The resolved palette book: base ramp + per-section palettes + per-status tints. */
export interface PaletteBook {
  base: Record<string, string>;
  sections: Record<string, SectionPalette>;
  statusTints: Record<string, StatusTint>;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function asRegion(v: unknown, where: string): SpriteRegion {
  if (!isObject(v)) throw new Error(`invalid region at ${where}`);
  for (const k of ["col", "row", "cols", "rows"] as const) {
    if (typeof v[k] !== "number") {
      throw new Error(`region ${where} missing numeric "${k}"`);
    }
  }
  return {
    col: v.col as number,
    row: v.row as number,
    cols: v.cols as number,
    rows: v.rows as number,
  };
}

/**
 * Parse + validate a manifest JSON. If `availableGrids` is supplied, every
 * sprite's `grid` ref MUST be present or the load throws (missing-grid rejection).
 */
export function loadManifest(
  json: unknown,
  availableGrids?: Iterable<string>,
): SpriteManifest {
  if (!isObject(json) || !isObject(json.sprites)) {
    throw new Error('invalid manifest: expected an object with a "sprites" map');
  }
  const grids = availableGrids ? new Set(availableGrids) : undefined;
  const sprites: Record<string, SpriteEntry> = {};
  for (const [name, raw] of Object.entries(json.sprites)) {
    if (!isObject(raw)) throw new Error(`invalid sprite entry "${name}"`);
    if (typeof raw.grid !== "string") {
      throw new Error(`sprite "${name}" missing string "grid" ref`);
    }
    if (grids && !grids.has(raw.grid)) {
      throw new Error(
        `sprite "${name}" references missing grid "${raw.grid}"`,
      );
    }
    if (typeof raw.cellWidth !== "number" || typeof raw.cellHeight !== "number") {
      throw new Error(`sprite "${name}" missing numeric cellWidth/cellHeight`);
    }
    if (!isObject(raw.regions)) {
      throw new Error(`sprite "${name}" missing "regions" map`);
    }
    const regions: Record<string, SpriteRegion> = {};
    for (const [rn, rv] of Object.entries(raw.regions)) {
      regions[rn] = asRegion(rv, `${name}.${rn}`);
    }
    sprites[name] = {
      grid: raw.grid,
      cellWidth: raw.cellWidth,
      cellHeight: raw.cellHeight,
      regions,
    };
  }
  const manifest: SpriteManifest = { sprites };
  if (isObject(json.cellDefault)) {
    const cd = json.cellDefault;
    if (typeof cd.width === "number" && typeof cd.height === "number") {
      manifest.cellDefault = { width: cd.width, height: cd.height };
    }
  }
  return manifest;
}

/**
 * Parse `palettes.json` — ONE base ramp + per-section palettes (resolved to
 * base-hex -> section-hex maps) + per-status tints (merged over the defaults).
 */
export function loadPalettes(json: unknown): PaletteBook {
  if (!isObject(json)) throw new Error("invalid palettes: expected an object");
  const base = isObject(json.base)
    ? (json.base as Record<string, string>)
    : { ...BASE_RAMP };

  const sections: Record<string, SectionPalette> = {};
  if (isObject(json.sections)) {
    for (const [name, ramp] of Object.entries(json.sections)) {
      if (!isObject(ramp)) throw new Error(`invalid section palette "${name}"`);
      sections[name] = sectionPaletteFromRamps(
        name,
        base,
        ramp as Record<string, string>,
      );
    }
  }

  const statusTints: Record<string, StatusTint> = { ...DEFAULT_STATUS_TINTS };
  if (isObject(json.statusTints)) {
    for (const [name, t] of Object.entries(json.statusTints)) {
      if (!isObject(t)) throw new Error(`invalid status tint "${name}"`);
      const prev = statusTints[name];
      statusTints[name] = {
        name,
        brightness:
          typeof t.brightness === "number" ? t.brightness : prev?.brightness ?? 1,
        saturation:
          typeof t.saturation === "number" ? t.saturation : prev?.saturation ?? 1,
        tint: Array.isArray(t.tint) ? (t.tint as StatusTint["tint"]) : prev?.tint,
        tintStrength:
          typeof t.tintStrength === "number"
            ? t.tintStrength
            : prev?.tintStrength,
      };
    }
  }

  return { base, sections, statusTints };
}
