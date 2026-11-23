// ghostopia ghost-renderer — public API.
//
// A thin TS/PixiJS renderer of server-authoritative state: a Zustand store
// bridge, pure camera math, a typed map-data loader, plus (Task 2) the PixiJS
// dt-clamped render loop + the directional animated ghost sprite. Imports NO
// GhostCrawl SDK and NO Python backend package — it reads map DATA + the
// @ghostopia/ghost-art atlas only.

export {
  useWorldStore,
  getWorldState,
  type WorldState,
} from "./store.js";

export {
  clampCamera,
  clampZoom,
  panByScreen,
  zoomByFactor,
  zoomAtPoint,
  followStep,
  leadPoint,
  boundsFromWorld,
  MIN_ZOOM,
  MAX_ZOOM,
  type Camera,
  type CameraBounds,
} from "./Camera.js";

export {
  pointerDistance,
  pointerMidpoint,
  pinchScale,
  type PointerPos,
} from "./gestures.js";

export {
  loadMapData,
  sectionForArea,
  areasForSection,
  type WorldMapData,
  type Bounds,
  type MapDestination,
  type Area,
  type PlacedProp,
} from "./mapData.js";

export { PropSprite, type PropSpriteOptions } from "./PropSprite.js";

export type {
  Bubble,
  Critter,
  Facing,
  Ghost,
  GhostAttention,
  GhostState,
  GhostStatusChanged,
  GhostWorkKind,
  Point,
} from "./contract.js";

export {
  CritterSprite,
  critterClipName,
  PET_FLASH_MS,
  type CritterSpriteUi,
} from "./CritterSprite.js";

export {
  createRenderLoop,
  MAX_DT_MS,
  DEFAULT_WORLD_THEME,
  type RenderLoopOptions,
  type RenderLoopHandle,
  type EditorHooks,
  type WorldTheme,
} from "./RenderLoop.js";

export {
  EditorOverlay,
  tileFromWorld,
  type EditorOverlayView,
  type OverlayProp,
  type OverlayArea,
  type OverlayDest,
  type OverlayPreview,
  type OverlaySelection,
} from "./editorOverlay.js";

export {
  GhostSprite,
  clipNameForGhost,
  resolveFacing,
  restingZzzVisible,
  actionKindForState,
  scaleColor,
  WORK_STATE_CLIP,
  type GhostSpriteOptions,
  type GhostSpriteUi,
  type FrameResolver,
} from "./GhostSprite.js";

export {
  makeActionGlyph,
  ACTION_GLYPH_KINDS,
  fillerStrip,
  identityBadge,
  hashId,
  hslToRgb,
  clampToBounds,
  BADGE_EMOJI,
  type IdentityBadge,
  type ActionGlyphKind,
} from "./overlays.js";

export {
  GhostFrameFactory,
  rampFromColor,
  ghostPaletteFor,
  ghostColorKey,
  colorToHex,
} from "./ghostRecolor.js";

export { hash2, type SectionTintMap } from "./visuals.js";

export {
  dashOffset,
  dashSegments,
  drawLinkLine,
  type DashSegment,
} from "./linklines.js";

export {
  makeFlourish,
  sampleParticle,
  seedFlourishParticles,
  ghostAlphaFor,
  easeOutCubic,
  FLOURISH_MS,
  type FlourishKind,
  type FlourishEffect,
  type FlourishParticle,
} from "./flourish.js";

export {
  pointInBox,
  topmostHit,
  isClick,
  CLICK_DRAG_THRESHOLD_PX,
  type HitBox,
  type HitEntity,
} from "./hitTest.js";
