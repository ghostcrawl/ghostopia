// ghostopia ghost-art — 1px procedural outline for crisp small-size readability.
//
// Mark every TRANSPARENT pixel that has at least one cardinal (N/S/E/W) opaque
// neighbour with the outline colour. Opaque interiors are unchanged; transparent
// pixels with no opaque neighbour stay transparent. Original re-implementation
// of the standard cardinal-neighbour edge pass.

import { cloneRaster, getPixel, setPixel, type Raster, type Rgba } from "./spriteGrid.js";

const DEFAULT_OUTLINE: Rgba = [27, 23, 48, 255]; // deep indigo (BASE_RAMP.outline)

/**
 * Return a new raster with a 1px cardinal-neighbour outline painted into the
 * transparent border pixels. The interior raster is unchanged.
 */
export function outline(raster: Raster, color: Rgba = DEFAULT_OUTLINE): Raster {
  const out = cloneRaster(raster);
  for (let y = 0; y < raster.height; y++) {
    for (let x = 0; x < raster.width; x++) {
      const [, , , a] = getPixel(raster, x, y);
      if (a !== 0) continue; // only paint into transparent pixels
      const n = getPixel(raster, x, y - 1)[3];
      const s = getPixel(raster, x, y + 1)[3];
      const e = getPixel(raster, x + 1, y)[3];
      const w = getPixel(raster, x - 1, y)[3];
      if (n > 0 || s > 0 || e > 0 || w > 0) {
        setPixel(out, x, y, color);
      }
    }
  }
  return out;
}
