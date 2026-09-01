// ghostopia ghost-art — the ONE cute-spooky base ramp + per-section palettes
// and per-status tints. One authored ghost family produces N looks purely by
// data: swap the base ramp for a section ramp (recolor) and multiply a status
// tint over it. Original palette, keyed off a single cohesive ramp.

import { hexToRgba, type Rgba } from "./spriteGrid.js";

/**
 * A resolved section palette: a map from a base hex colour (lowercased) to the
 * section's replacement hex. `recolor` walks a raster and swaps matching pixels.
 */
export interface SectionPalette {
  name: string;
  /** baseHex(lowercased) -> sectionHex */
  map: Record<string, string>;
}

/** A per-status colour transform: brightness/saturation multiply + optional tint blend. */
export interface StatusTint {
  name: string;
  /** rgb multiply (1 = unchanged). */
  brightness: number;
  /** 0 = greyscale, 1 = unchanged, >1 = punchier. */
  saturation: number;
  /** optional additive colour blend. */
  tint?: Rgba;
  /** 0..1 blend strength for `tint`. */
  tintStrength?: number;
}

/**
 * The ONE cute-spooky base ghost ramp (desaturated indigo/teal night base,
 * off-white body with a cyan rim, dark eyes, warm blush). Section palettes
 * hue-shift off THIS single ramp so the world stays cohesive.
 */
export const BASE_RAMP: Record<string, string> = {
  body: "#e8ecff", // off-white ghost body
  bodyShade: "#b0b8e0", // cool shade under the body
  rim: "#8ff0ff", // cyan rim light (ghostly glow edge)
  eye: "#101018", // near-black eyes
  blush: "#ff9ec4", // soft warm blush
  outline: "#1b1730", // deep indigo outline
};

/** The default per-status tint table (data-overridable via palettes.json). */
export const DEFAULT_STATUS_TINTS: Record<string, StatusTint> = {
  error: { name: "error", brightness: 0.68, saturation: 0.35, tint: [120, 40, 60, 255], tintStrength: 0.18 },
  retry: { name: "retry", brightness: 0.85, saturation: 0.7 },
  success: { name: "success", brightness: 1.22, saturation: 1.15, tint: [120, 255, 190, 255], tintStrength: 0.12 },
  working: { name: "working", brightness: 1.06, saturation: 1.05 },
  idle: { name: "idle", brightness: 1.0, saturation: 1.0 },
};

/** Rec.601-ish perceived luminance of an RGB triple. */
export function luma(r: number, g: number, b: number): number {
  return 0.299 * r + 0.587 * g + 0.114 * b;
}

/** Clamp to a 0..255 byte. */
export function clampByte(v: number): number {
  return v < 0 ? 0 : v > 255 ? 255 : Math.round(v);
}

/**
 * Build a `SectionPalette` from a base ramp and a section ramp that share KEYS.
 * The resulting map is keyed by the base RAMP's resolved hex (lowercased) so it
 * can be applied to an already-resolved raster.
 */
export function sectionPaletteFromRamps(
  name: string,
  baseRamp: Record<string, string>,
  sectionRamp: Record<string, string>,
): SectionPalette {
  const map: Record<string, string> = {};
  for (const key of Object.keys(sectionRamp)) {
    const baseHex = baseRamp[key];
    if (baseHex === undefined) continue; // section overrides an unknown key -> skip
    map[normalizeHex(baseHex)] = sectionRamp[key];
  }
  return { name, map };
}

/** Normalize any hex form to a canonical lowercase `#rrggbb` (or `#rrggbbaa`). */
export function normalizeHex(hex: string): string {
  const [r, g, b, a] = hexToRgba(hex);
  const h = (n: number) => n.toString(16).padStart(2, "0");
  const rgb = `#${h(r)}${h(g)}${h(b)}`;
  return a === 255 ? rgb : `${rgb}${h(a)}`;
}
