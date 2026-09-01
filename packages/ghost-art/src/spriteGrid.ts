// ghostopia ghost-art — original JSON pixel-grid model + palette resolve.
//
// A sprite is authored as a grid of palette KEYS (`SpriteGrid`). Resolving it
// walks each key to its hex colour and yields a flat RGBA `Raster` (canvas
// ImageData-compatible). The empty-string key `''` is transparent — never a
// silent black. An unknown key throws so authoring mistakes surface loudly.
//
// This is an ORIGINAL re-implementation of the "JSON grid + palette" idea, not
// a copy of any reference asset or code.

/** A grid of palette KEYS. `'' = transparent`. Rows may be ragged (right-padded). */
export type PixelGrid = string[][];

/** An authored sprite: a palette (key -> hex) plus a grid of keys. */
export interface SpriteGrid {
  palette: Record<string, string>;
  pixels: PixelGrid;
}

/** A flat RGBA raster (4 bytes/pixel, row-major) — Pixi/canvas consumable. */
export interface Raster {
  width: number;
  height: number;
  data: Uint8ClampedArray;
}

/** RGBA tuple. */
export type Rgba = [number, number, number, number];

/** A compact authoring form: char-rows + a legend, expanded to a `SpriteGrid`. */
export interface CompactGrid {
  legend: Record<string, string>;
  /** the char that means transparent (default a single space). */
  transparent?: string;
  rows: string[];
}

const HEX_RE = /^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$/;

/** Parse `#rgb` / `#rgba` / `#rrggbb` / `#rrggbbaa` (with or without `#`) to RGBA. */
export function hexToRgba(hex: string): Rgba {
  const m = HEX_RE.exec(hex.trim());
  if (!m) throw new Error(`invalid hex colour: ${JSON.stringify(hex)}`);
  let h = m[1];
  if (h.length === 3 || h.length === 4) {
    h = h
      .split("")
      .map((c) => c + c)
      .join("");
  }
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  const a = h.length === 8 ? parseInt(h.slice(6, 8), 16) : 255;
  return [r, g, b, a];
}

/** Read the RGBA tuple at (x, y). Out-of-bounds reads return transparent. */
export function getPixel(raster: Raster, x: number, y: number): Rgba {
  if (x < 0 || y < 0 || x >= raster.width || y >= raster.height) {
    return [0, 0, 0, 0];
  }
  const i = (y * raster.width + x) * 4;
  const d = raster.data;
  return [d[i], d[i + 1], d[i + 2], d[i + 3]];
}

/** Write the RGBA tuple at (x, y). Out-of-bounds writes are ignored. */
export function setPixel(raster: Raster, x: number, y: number, px: Rgba): void {
  if (x < 0 || y < 0 || x >= raster.width || y >= raster.height) return;
  const i = (y * raster.width + x) * 4;
  raster.data[i] = px[0];
  raster.data[i + 1] = px[1];
  raster.data[i + 2] = px[2];
  raster.data[i + 3] = px[3];
}

/** Allocate a fully-transparent raster. */
export function makeRaster(width: number, height: number): Raster {
  return { width, height, data: new Uint8ClampedArray(width * height * 4) };
}

/** Deep-copy a raster (so transforms never mutate their input). */
export function cloneRaster(raster: Raster): Raster {
  return {
    width: raster.width,
    height: raster.height,
    data: new Uint8ClampedArray(raster.data),
  };
}

/**
 * Resolve a `SpriteGrid` to an RGBA `Raster`.
 * - `'' -> transparent` (never a silent black)
 * - unknown key -> throws (authoring mistakes are loud)
 * - ragged rows are right-padded with transparent pixels
 */
export function resolveGrid(grid: SpriteGrid): Raster {
  const height = grid.pixels.length;
  let width = 0;
  for (const row of grid.pixels) width = Math.max(width, row.length);
  const raster = makeRaster(width, height);
  for (let y = 0; y < height; y++) {
    const row = grid.pixels[y];
    for (let x = 0; x < row.length; x++) {
      const key = row[x];
      if (key === "") continue; // transparent
      const hex = grid.palette[key];
      if (hex === undefined) {
        throw new Error(
          `unknown palette key ${JSON.stringify(key)} at (${x}, ${y})`,
        );
      }
      setPixel(raster, x, y, hexToRgba(hex));
    }
  }
  return raster;
}

/** Expand a compact `{ legend, transparent, rows }` authoring form to a `SpriteGrid`. */
export function expandGrid(compact: CompactGrid): SpriteGrid {
  const t = compact.transparent ?? " ";
  const pixels: PixelGrid = compact.rows.map((row) =>
    [...row].map((ch) => (ch === t ? "" : ch)),
  );
  return { palette: { ...compact.legend }, pixels };
}
