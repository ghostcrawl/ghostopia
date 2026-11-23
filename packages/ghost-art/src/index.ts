// ghostopia ghost-art — original procedural pixel-art pipeline (public API).
//
// JSON pixel-grids + a palette resolve to an RGBA raster; a section palette
// recolors the ONE ghost family per section, a status tint colours it per
// status, a 1px outline keeps it crisp, and a data-driven manifest packs
// everything into a PixiJS-consumable atlas. Frontend TS only — imports NO
// GhostCrawl SDK and NO Python backend package.

export {
  resolveGrid,
  expandGrid,
  hexToRgba,
  getPixel,
  setPixel,
  makeRaster,
  cloneRaster,
  type SpriteGrid,
  type PixelGrid,
  type CompactGrid,
  type Raster,
  type Rgba,
} from "./spriteGrid.js";

export {
  BASE_RAMP,
  DEFAULT_STATUS_TINTS,
  sectionPaletteFromRamps,
  normalizeHex,
  clampByte,
  luma,
  type SectionPalette,
  type StatusTint,
} from "./palette.js";

export { recolor, applyStatusTint } from "./recolor.js";
export { outline } from "./outline.js";

export {
  loadManifest,
  loadPalettes,
  type SpriteManifest,
  type SpriteEntry,
  type SpriteRegion,
  type PaletteBook,
} from "./manifest.js";

export {
  buildAtlas,
  type Atlas,
  type AtlasFrame,
  type BuildAtlasOptions,
} from "./buildAtlas.js";

export {
  facingFromVector,
  MIRROR_SOURCE,
  type Facing8,
  type FacingResult,
} from "./facing.js";

export {
  loadAnimations,
  type Frame,
  type Clip,
  type AnimationBook,
} from "./animations.js";

export {
  loadPropCatalog,
  resolvePropRegion,
  type PropCatalog,
  type PropDef,
  type PropOrientation,
  type Footprint,
  type Placement,
  type ResolvedPropRegion,
} from "./propCatalog.js";
