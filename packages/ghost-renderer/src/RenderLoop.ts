// ghostopia ghost-renderer — the PixiJS dt-clamped render loop (Pattern 5).
//
// The world animates in the PixiJS ticker OUTSIDE React: each frame computes a
// CLAMPED `dt`, reads the Zustand store via `getState()` (NO React re-render per
// frame), applies the camera transform to a root Container, drifts the OVERHEAD
// ambient (mist/bats/wisps), and syncs a `GhostSprite` per ghost. Pixels are crisp
// — `imageSmoothingEnabled = false` on every upload + `scaleMode: 'nearest'` globally.
//
// It draws a POLISHED NIGHT graveyard from `WorldMapData` (regions/graves/workstations)
// + the atlas: a dark navy/indigo STATIC ground (the drifting-fog-over-ground
// is gone, so a still camera shows a still floor), per-section role-tinted washes that
// read as glows, deterministic tile variety + graveyardy decor, grounding drop-shadows,
// a STATIC edge vignette + a moon/star night backdrop for depth, OVERHEAD-only ambient
// motion, and ANY-colour ghosts (shared clips). Imports only PixiJS + the pure
// @ghostopia/ghost-art helpers — NO GhostCrawl SDK and NO Python backend package.

import {
  Application,
  Container,
  Graphics,
  Rectangle,
  Sprite,
  Text,
  Texture,
  TextureSource,
  TilingSprite,
} from "pixi.js";
import type { Atlas, AnimationBook, PaletteBook, PropCatalog } from "@ghostopia/ghost-art";

import { GhostSprite, type FrameResolver } from "./GhostSprite.js";
import { EditorOverlay, tileFromWorld, type EditorOverlayView } from "./editorOverlay.js";
import { CritterSprite } from "./CritterSprite.js";
import { PropSprite } from "./PropSprite.js";
import { makeFlourish, ghostAlphaFor, FLOURISH_MS, type FlourishEffect } from "./flourish.js";
import { initialPresence, isGraveRestState, stepPresence, type PresenceState } from "./graveRest.js";
import { dashOffset, drawLinkLine } from "./linklines.js";
import { hashId } from "./overlays.js";
import { leadPoint } from "./Camera.js";
import { GhostFrameFactory } from "./ghostRecolor.js";
import { useWorldStore } from "./store.js";
import { isClick, topmostHit, type HitEntity } from "./hitTest.js";
import { pointerDistance, pointerMidpoint, pinchScale, type PointerPos } from "./gestures.js";
import type { SectionTintMap } from "./visuals.js";
import { hash2 } from "./visuals.js";
import type { WorldMapData } from "./mapData.js";

/** Frame delta clamp (ms) — prevents a teleport on tab refocus / long GC pause. */
export const MAX_DT_MS = 100;

/**
 * Night presentation constants (from `palettes.json` `world`) — the page/ground
 * darkness, the STATIC vignette, and the moon/star backdrop tints. This is pure
 * presentation, NOT the ghost recolor ramp (the ghost family is unchanged).
 */
export interface WorldTheme {
  /** page background (deep night). */
  background: number;
  /** fallback ground fill when the night grass tile is unavailable. */
  groundTint: number;
  /** static edge-vignette colour (0xRRGGBB). */
  vignette: number;
  /** static edge-vignette max alpha. */
  vignetteAlpha: number;
  /** per-section wash base alpha (glow-pop over the dark ground). */
  washAlpha: number;
  /** moon body tint (also the moon-glow bloom seed). */
  moon: number;
  /** moon halo glow colour. */
  moonGlow: number;
  /** star tint. */
  star: number;
}

/** The default night theme (used when `palettes.json` omits a `world` block). */
export const DEFAULT_WORLD_THEME: WorldTheme = {
  background: 0x080814,
  groundTint: 0x12183a,
  vignette: 0x04030d,
  vignetteAlpha: 0.66,
  washAlpha: 0.17,
  moon: 0xf6f3dc,
  moonGlow: 0x5a6bb0,
  star: 0xcfe0ff,
};

export interface RenderLoopOptions {
  canvas: HTMLCanvasElement;
  mapData: WorldMapData;
  atlas: Atlas;
  book: AnimationBook;
  /** the resolved palette book (base ramp + section palettes) — ghost recolor. */
  paletteBook: PaletteBook;
  /** the placeable-prop catalog — the renderer draws every placed prop from this. */
  catalog: PropCatalog;
  /** section-name AND region-id -> base tint colour (0xRRGGBB) for washes. */
  sectionTints: SectionTintMap;
  /** page background colour (default deep graveyard indigo). Overridden by `theme.background`. */
  background?: number;
  /** night presentation theme (ground/vignette/backdrop tints). Defaults to {@link DEFAULT_WORLD_THEME}. */
  theme?: WorldTheme;
  /**
   * Optional canvas click-to-select: on a click (pointerup with negligible drag) over a ghost
   * sprite, called with that ghost's id; a click that misses every ghost passes `null` (a
   * deselect / no-op). Wired to the SAME `ghost.select` the roster row fires.
   */
  onSelectGhost?: (ghostId: string | null) => void;
  /**
   * Optional canvas click-to-pet: on a click over a CRITTER sprite, called with that critter's
   * id (the render loop sends a `critter.pet` so the server acks a heart/spark flash). A critter
   * hit takes precedence over a ghost select at the same point (critters are the foreground toy).
   */
  onPetCritter?: (critterId: string) => void;
  /**
   * Optional canvas click-to-inspect-a-DEPARTMENT (196): on a click that MISSES every ghost +
   * critter but lands inside a labelled map area, called with that area's section id — the app
   * opens that department's found results (the "click a department to read its findings" business
   * model). A click on bare ground (no area) passes `null` so the app can dismiss the card.
   */
  onSelectSection?: (sectionId: string | null) => void;
  /**
   * Optional department-plot predicate. Given the section id under the pointer (or
   * null on bare ground), returns whether it is a clickable DEPARTMENT plot — the render loop
   * swaps the stage cursor to `pointer` over a department and `grab` elsewhere (the hover
   * affordance). Without it the cursor stays the CSS default.
   */
  isDepartmentSection?: (sectionId: string | null) => boolean;
  /**
   * Optional Graveyard Builder hooks. When provided, the render loop draws the editor
   * overlay (grid + draft props + preview + selection) whenever `getView().active`, and routes
   * canvas taps/hover to `onTile`/`onHover` (a tile coord) instead of ghost-select. The camera
   * pan/pinch/zoom stays live so the editor is mobile-usable.
   */
  editor?: EditorHooks;
}

/** The editor's canvas hooks (draw + interaction). */
export interface EditorHooks {
  /** the per-frame overlay view (active flag + draft props/plots/dests/preview/selection). */
  getView: () => EditorOverlayView;
  /** a tile was tapped (left = tool action, right = quick-erase). */
  onTile: (tileX: number, tileY: number, button: "left" | "right") => void;
  /** the cursor hovered a tile (drives the held-prop placement preview). */
  onHover: (tileX: number, tileY: number) => void;
}

/** Handle to a running render loop. */
export interface RenderLoopHandle {
  app: Application;
  /** Swap the live world in place (after a validated map.save/reset rebroadcast) without
   * re-initialising the PixiJS Application. Rebuilds only the static content; ghosts keep running. */
  reloadMap: (mapData: WorldMapData) => void;
  destroy: () => void;
}

/** Build the atlas base texture from raw RGBA data (nearest, no smoothing). */
function atlasToTexture(atlas: Atlas): Texture {
  const off = document.createElement("canvas");
  off.width = atlas.width;
  off.height = atlas.height;
  const ctx = off.getContext("2d");
  if (!ctx) throw new Error("render loop: 2D context unavailable for atlas upload");
  ctx.imageSmoothingEnabled = false; // must NOT smooth the pixel art
  const rgba = new Uint8ClampedArray(atlas.data);
  ctx.putImageData(new ImageData(rgba, atlas.width, atlas.height), 0, 0);
  const tex = Texture.from(off);
  tex.source.scaleMode = "nearest";
  return tex;
}

/** Slice the atlas frame table into per-"sprite:region" textures. */
function sliceFrames(atlas: Atlas, base: Texture): Record<string, Texture> {
  const frames: Record<string, Texture> = {};
  for (const [key, f] of Object.entries(atlas.frames)) {
    frames[key] = new Texture({ source: base.source, frame: new Rectangle(f.x, f.y, f.w, f.h) });
  }
  return frames;
}

/** `0xRRGGBB` -> `"r,g,b"` for a CSS rgba() string. */
function rgbTriplet(color: number): string {
  return `${(color >> 16) & 0xff},${(color >> 8) & 0xff},${color & 0xff}`;
}

/** True when ANY ghost in the map currently needs the operator (attention.needs). */
function hasAttention(ghosts: Record<string, { attention?: { needs: boolean } | null }>): boolean {
  for (const g of Object.values(ghosts)) if (g.attention?.needs) return true;
  return false;
}

/**
 * A procedural radial vignette texture sized to the screen (transparent -> dark).
 * STATIC — built once per screen size and never mutated per-frame; it supplies the
 * night depth (the drifting ground fog is gone), so a still camera shows a still floor.
 */
function makeVignetteTexture(w: number, h: number, theme: WorldTheme): Texture {
  const c = document.createElement("canvas");
  c.width = Math.max(1, w);
  c.height = Math.max(1, h);
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("render loop: 2D context unavailable for vignette");
  const g = ctx.createRadialGradient(
    w / 2,
    h / 2,
    Math.min(w, h) * 0.32,
    w / 2,
    h / 2,
    Math.max(w, h) * 0.76,
  );
  const rgb = rgbTriplet(theme.vignette);
  g.addColorStop(0, `rgba(${rgb},0)`);
  g.addColorStop(1, `rgba(${rgb},${theme.vignetteAlpha})`);
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  return Texture.from(c);
}

/**
 * A screen-space RED edge vignette texture for the ATTENTION alert: fully
 * transparent through the middle, ember-red creeping in only at the very edges, so a
 * throbbing alpha reads as a "something needs you" pulse WITHOUT obscuring the world.
 * Built once per screen size (alpha is animated on the sprite, not rebaked).
 */
function makeAttentionVignetteTexture(w: number, h: number): Texture {
  const c = document.createElement("canvas");
  c.width = Math.max(1, w);
  c.height = Math.max(1, h);
  const ctx = c.getContext("2d");
  if (!ctx) throw new Error("render loop: 2D context unavailable for attention vignette");
  const g = ctx.createRadialGradient(
    w / 2,
    h / 2,
    Math.min(w, h) * 0.5,
    w / 2,
    h / 2,
    Math.max(w, h) * 0.72,
  );
  g.addColorStop(0, "rgba(224,87,74,0)");
  g.addColorStop(0.82, "rgba(224,87,74,0)");
  g.addColorStop(1, "rgba(224,87,74,0.9)");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  return Texture.from(c);
}

/**
 * Build the STATIC night sky backdrop (screen-space, behind the world): a soft
 * radial night-glow, a scattered deterministic star field, and a large moon with
 * a halo bloom. Built once per screen size; nothing here animates per-frame, so it
 * never reads as the floor moving.
 */
function buildBackdrop(
  frames: Record<string, Texture>,
  theme: WorldTheme,
  w: number,
  h: number,
): Container {
  const backdrop = new Container();
  backdrop.label = "backdrop";
  backdrop.eventMode = "none";

  // a gentle top-down night gradient wash so the sky band reads above the ground
  const glow = document.createElement("canvas");
  glow.width = Math.max(1, w);
  glow.height = Math.max(1, h);
  const gctx = glow.getContext("2d");
  if (gctx) {
    const grad = gctx.createLinearGradient(0, 0, 0, h);
    const mg = rgbTriplet(theme.moonGlow);
    grad.addColorStop(0, `rgba(${mg},0.22)`);
    grad.addColorStop(0.45, `rgba(${mg},0.05)`);
    grad.addColorStop(1, `rgba(${mg},0)`);
    gctx.fillStyle = grad;
    gctx.fillRect(0, 0, w, h);
    const gs = new Sprite(Texture.from(glow));
    gs.eventMode = "none";
    backdrop.addChild(gs);
  }

  // deterministic star field across the upper band (stable every load)
  const starTex = frames["star:base"];
  if (starTex) {
    const count = Math.max(24, Math.floor((w * h) / 26000));
    for (let i = 0; i < count; i++) {
      const sx = Math.floor(hash2(i, 7, 41) * w);
      const sy = Math.floor(hash2(i, 13, 42) * h * 0.72);
      const s = new Sprite(starTex);
      s.anchor.set(0.5);
      s.position.set(sx, sy);
      s.tint = theme.star;
      s.alpha = 0.35 + hash2(i, 3, 43) * 0.5;
      const sc = 0.6 + hash2(i, 9, 44) * 0.9;
      s.scale.set(sc);
      s.eventMode = "none";
      backdrop.addChild(s);
    }
  }

  // the moon — a halo bloom + the pixel disc, up in the top-right sky band
  const moonTex = frames["moon:base"];
  if (moonTex) {
    const mx = w * 0.8;
    const my = h * 0.2;
    const halo = new Graphics()
      .circle(mx, my, moonTex.width * 1.7)
      .fill({ color: theme.moonGlow, alpha: 0.16 });
    halo.blendMode = "add";
    halo.eventMode = "none";
    backdrop.addChild(halo);
    const halo2 = new Graphics()
      .circle(mx, my, moonTex.width * 1.05)
      .fill({ color: theme.moon, alpha: 0.12 });
    halo2.blendMode = "add";
    halo2.eventMode = "none";
    backdrop.addChild(halo2);
    const moon = new Sprite(moonTex);
    moon.anchor.set(0.5);
    moon.position.set(mx, my);
    moon.scale.set(2);
    moon.eventMode = "none";
    backdrop.addChild(moon);
  }

  return backdrop;
}

/**
 * Build an ORIGINAL pixel speech/thought bubble container for a ghost's `say`/`overlay`.
 * A `say` gets a pointed tail; an `overlay` gets a dotted thought tail. The container's pivot
 * is its tail tip so positioning it places the tail just above the ghost's head. Text is the
 * server-provided line only — this draws it, never authors copy.
 */
function makeBubbleContainer(text: string, kind: "say" | "overlay"): Container {
  const c = new Container();
  c.label = "bubble";
  const pad = 4;
  const label = new Text({
    text,
    style: {
      fontFamily: "ui-monospace, monospace",
      fontSize: 11,
      fontWeight: "600",
      fill: 0x1a1626,
      align: "center",
    },
  });
  label.resolution = 2; // crisp text at the pixel scale
  const w = Math.ceil(label.width) + pad * 2;
  const h = Math.ceil(label.height) + pad * 2;
  const body = 0xf3eefe;
  const bg = new Graphics();
  bg.roundRect(0, 0, w, h, 5)
    .fill({ color: body, alpha: 0.96 })
    .stroke({ color: 0x2a2340, width: 1, alpha: 0.85 });
  const tailY = kind === "say" ? 5 : 8;
  if (kind === "say") {
    bg.moveTo(w / 2 - 4, h)
      .lineTo(w / 2, h + tailY)
      .lineTo(w / 2 + 4, h)
      .fill({ color: body, alpha: 0.96 });
  } else {
    bg.circle(w / 2 - 1, h + 3, 2).fill({ color: body, alpha: 0.9 });
    bg.circle(w / 2 + 2, h + 7, 1.4).fill({ color: body, alpha: 0.82 });
  }
  label.position.set(pad, pad);
  c.addChild(bg);
  c.addChild(label);
  c.pivot.set(w / 2, h + tailY); // bottom-centre / tail tip
  return c;
}

/** Place a bottom-anchored world sprite at a TILE coordinate's bottom-centre. */
function placeAtTile(sprite: Sprite, tileX: number, tileY: number, tileSize: number): void {
  sprite.anchor.set(0.5, 1);
  sprite.x = tileX * tileSize + tileSize / 2;
  sprite.y = tileY * tileSize + tileSize; // sit on the bottom edge of the tile
}

/** Draw a soft elliptical ground shadow (world coords) into a Graphics. */
function drawShadow(g: Graphics, cx: number, cy: number, rx: number, ry: number): void {
  g.ellipse(cx, cy, rx, ry).fill({ color: 0x0a0812, alpha: 0.26 });
}

/** One drifting OVERHEAD ambient element (mist puff / bat / wisp). */
interface OverheadEntity {
  sprite: Sprite;
  x: number;
  baseY: number;
  /** world px per ms (signed — drift direction). */
  vx: number;
  bobAmp: number;
  /** bob angular rate (rad/ms). */
  bobHz: number;
  t: number;
  /** optional 2+ frame cycle (bat flap / wisp flicker). */
  clip?: Texture[];
  frameMs?: number;
  frameAcc?: number;
  frameIdx?: number;
}

/** A running overhead ambient (mist/bats/wisps) with a per-frame `update`. */
interface OverheadAmbient {
  entities: OverheadEntity[];
  /** world width for drift wrap-around. */
  worldW: number;
  /** advance the drift + frame cycles for a clamped dt (no-op under reduced motion). */
  update: (dt: number, reduceMotion: boolean) => void;
}

/**
 * Populate the OVERHEAD ambient layer with slow-drifting mist puffs;
 * bats/wisps are pushed in by {@link addFlyers} (task 2). Everything here lives ABOVE
 * the ghost layer and is alpha-light, so it clearly reads as *above the world* — it is
 * the ONLY moving world element and never touches the static floor.
 */
function buildOverhead(
  overheadLayer: Container,
  frames: Record<string, Texture>,
  worldW: number,
  worldH: number,
): OverheadAmbient {
  const entities: OverheadEntity[] = [];
  const ambient: OverheadAmbient = {
    entities,
    worldW,
    update: (dt, reduceMotion) => {
      if (reduceMotion) return; // prefers-reduced-motion: overhead drift disabled entirely
      const margin = 80;
      for (const e of entities) {
        e.t += dt;
        e.x += e.vx * dt;
        if (e.x > worldW + margin) e.x = -margin;
        else if (e.x < -margin) e.x = worldW + margin;
        e.sprite.x = e.x;
        e.sprite.y = e.baseY + Math.sin(e.t * e.bobHz) * e.bobAmp;
        if (e.clip && e.clip.length > 1) {
          e.frameAcc = (e.frameAcc ?? 0) + dt;
          const ms = e.frameMs ?? 200;
          if (e.frameAcc >= ms) {
            e.frameAcc -= ms;
            e.frameIdx = ((e.frameIdx ?? 0) + 1) % e.clip.length;
            e.sprite.texture = e.clip[e.frameIdx];
          }
        }
      }
    },
  };

  // ---- mist puffs: sparse, additive, alpha-light drifting patches ----
  const mistTex = frames["fog:base"];
  if (mistTex) {
    const puffs = Math.max(5, Math.floor(worldW / 220));
    for (let i = 0; i < puffs; i++) {
      const s = new Sprite(mistTex);
      s.anchor.set(0.5);
      s.eventMode = "none";
      s.blendMode = "add";
      s.alpha = 0.05 + hash2(i, 1, 61) * 0.06;
      s.scale.set(3 + hash2(i, 2, 62) * 3);
      s.tint = 0xbcc6ea;
      const x = hash2(i, 3, 63) * worldW;
      const baseY = hash2(i, 4, 64) * worldH;
      s.position.set(x, baseY);
      overheadLayer.addChild(s);
      entities.push({
        sprite: s,
        x,
        baseY,
        vx: (hash2(i, 5, 65) < 0.5 ? -1 : 1) * (0.004 + hash2(i, 6, 66) * 0.004),
        bobAmp: 1 + hash2(i, 7, 67) * 2,
        bobHz: 0.0006 + hash2(i, 8, 68) * 0.0006,
        t: hash2(i, 9, 69) * 6000,
      });
    }
  }

  return ambient;
}

/**
 * Add drifting flyers (bats / will-o'-wisps) to a running overhead ambient — original
 * 2-frame sprites, OVERHEAD only, alpha-light. Called in task 2 once the bat/wisp
 * frames exist in the atlas; a missing frame is silently skipped.
 */
function addFlyers(
  ambient: OverheadAmbient,
  overheadLayer: Container,
  frames: Record<string, Texture>,
  worldH: number,
): void {
  const worldW = ambient.worldW;
  const bat0 = frames["bat:flap0"];
  const bat1 = frames["bat:flap1"];
  if (bat0 && bat1) {
    const bats = Math.max(3, Math.floor(worldW / 320));
    for (let i = 0; i < bats; i++) {
      const s = new Sprite(bat0);
      s.anchor.set(0.5);
      s.eventMode = "none";
      s.alpha = 0.85;
      s.scale.set(1);
      const x = hash2(i, 11, 71) * worldW;
      const baseY = hash2(i, 12, 72) * worldH * 0.5; // bats fly high
      s.position.set(x, baseY);
      overheadLayer.addChild(s);
      ambient.entities.push({
        sprite: s,
        x,
        baseY,
        vx: (hash2(i, 13, 73) < 0.5 ? -1 : 1) * (0.02 + hash2(i, 14, 74) * 0.02),
        bobAmp: 4 + hash2(i, 15, 75) * 5,
        bobHz: 0.002 + hash2(i, 16, 76) * 0.002,
        t: hash2(i, 17, 77) * 4000,
        clip: [bat0, bat1],
        frameMs: 130 + Math.floor(hash2(i, 18, 78) * 90),
        frameAcc: 0,
        frameIdx: 0,
      });
    }
  }

  const wisp0 = frames["wisp:glow0"];
  const wisp1 = frames["wisp:glow1"];
  if (wisp0 && wisp1) {
    const wisps = Math.max(3, Math.floor(worldW / 300));
    for (let i = 0; i < wisps; i++) {
      const s = new Sprite(wisp0);
      s.anchor.set(0.5);
      s.eventMode = "none";
      s.blendMode = "add";
      s.alpha = 0.7;
      const x = hash2(i, 21, 81) * worldW;
      const baseY = worldH * (0.4 + hash2(i, 22, 82) * 0.5); // wisps hover low-mid
      s.position.set(x, baseY);
      overheadLayer.addChild(s);
      ambient.entities.push({
        sprite: s,
        x,
        baseY,
        vx: (hash2(i, 23, 83) < 0.5 ? -1 : 1) * (0.006 + hash2(i, 24, 84) * 0.006),
        bobAmp: 3 + hash2(i, 25, 85) * 4,
        bobHz: 0.0016 + hash2(i, 26, 86) * 0.0018,
        t: hash2(i, 27, 87) * 5000,
        clip: [wisp0, wisp1],
        frameMs: 260 + Math.floor(hash2(i, 28, 88) * 160),
        frameAcc: 0,
        frameIdx: 0,
      });
    }
  }
}

/**
 * Build the static, polished world layer. Returns the world root (camera target),
 * the ghost layer to sync per frame, and an OVERHEAD layer (above the ghosts) for
 * ambient mist/bats. The ground layers are built ONCE and never mutated per-frame,
 * so a still camera shows a perfectly still floor (the old drifting-fog-over-ground
 * TilingSprite is gone — depth now comes from the static vignette + night backdrop).
 */
/** A reactive crypt-terminal prop the renderer powers on when the server marks it active. */
interface PropTerminal {
  sprite: Sprite;
  glow: Graphics;
  idleFrame: Texture;
  activeFrames: Texture[];
  frameAcc: number;
  frameIdx: number;
}

function buildWorld(
  mapData: WorldMapData,
  frames: Record<string, Texture>,
  sectionTints: SectionTintMap,
  theme: WorldTheme,
  catalog: PropCatalog,
  book: AnimationBook,
): {
  worldRoot: Container;
  /** the STATIC world content (ground/props/plots) as ONE container — swapped by reloadMap. */
  staticContent: Container;
  ghostLayer: Container;
  overheadLayer: Container;
  propTerminals: Map<string, PropTerminal>;
  /** placed props with a flicker clip (lantern/candle) the ticker advances. */
  animatedProps: PropSprite[];
} {
  const ts = mapData.tileSize;
  const worldW = mapData.width * ts;
  const worldH = mapData.height * ts;

  // All STATIC world content (ground, spine, noise, washes, plots, shadows, props) goes into
  // ONE container so the editor's map.save/reset can swap the whole world in place
  // (rebuild static content only) WITHOUT destroying + re-initialising the PixiJS Application
  // (re-init on the same canvas wedges headless WebGL).
  const staticContent = new Container();
  staticContent.label = "staticContent";

  const worldRoot = new Container();
  worldRoot.label = "worldRoot";

  // ---- 1. ground: tile the whole field with the night grass pixel-art tile ----
  const grass = frames["tile_grass:base"];
  if (grass) {
    staticContent.addChild(new TilingSprite({ texture: grass, width: worldW, height: worldH }));
  } else {
    staticContent.addChild(new Graphics().rect(0, 0, worldW, worldH).fill({ color: theme.groundTint }));
  }

  // ---- 2. tile variety: a deterministic path spine + dirt patches + noise ----
  const pathTex = frames["tile_path:base"];
  const dirtTex = frames["tile_dirt:base"];
  const occupied = new Set<string>();
  const spine = new Container();
  // vertical + horizontal path spine connecting the yard
  const spineX = Math.floor(mapData.width / 2);
  const spineY = Math.floor(mapData.height / 2) - 1;
  if (pathTex) {
    for (let y = 0; y < mapData.height; y++) {
      for (const tx of [spineX - 1, spineX]) {
        const s = new Sprite(pathTex);
        s.x = tx * ts;
        s.y = y * ts;
        s.alpha = 0.9 + hash2(tx, y, 3) * 0.1;
        spine.addChild(s);
      }
    }
    for (let x = 0; x < mapData.width; x++) {
      const s = new Sprite(pathTex);
      s.x = x * ts;
      s.y = spineY * ts;
      s.alpha = 0.85 + hash2(x, spineY, 4) * 0.15;
      spine.addChild(s);
    }
  }
  // dirt patches around graves (a worn look around ghost homes)
  if (dirtTex) {
    for (const grave of mapData.graves) {
      for (const [ox, oy] of [
        [0, 0],
        [1, 0],
        [0, 1],
      ]) {
        if (hash2(grave.x + ox, grave.y + oy, 7) < 0.55) continue;
        const s = new Sprite(dirtTex);
        s.x = (grave.x + ox) * ts;
        s.y = (grave.y + oy) * ts;
        s.alpha = 0.7;
        spine.addChild(s);
      }
    }
  }
  staticContent.addChild(spine);

  // subtle per-cell luminance noise so the grass isn't a flat uniform grid
  const noise = new Graphics();
  for (let y = 0; y < mapData.height; y++) {
    for (let x = 0; x < mapData.width; x++) {
      const n = hash2(x, y, 99);
      if (n > 0.82) noise.rect(x * ts, y * ts, ts, ts).fill({ color: 0xffffff, alpha: 0.04 });
      else if (n < 0.14) noise.rect(x * ts, y * ts, ts, ts).fill({ color: 0x000000, alpha: 0.06 });
    }
  }
  staticContent.addChild(noise);

  // ---- 3. per-section role-tinted ground washes with a soft inner glow ----
  const washes = new Graphics();
  for (const [id, b] of Object.entries(mapData.regions)) {
    const color = sectionTints[id] ?? 0x5a5080;
    const x = b.x * ts;
    const y = b.y * ts;
    const w = b.w * ts;
    const h = b.h * ts;
    const r = Math.min(10, ts * 0.6);
    // base wash — reads as a coloured glow POPPING against the dark night ground
    washes.roundRect(x + 1, y + 1, w - 2, h - 2, r).fill({ color, alpha: theme.washAlpha });
    // soft inner glow: two inset strokes fading inward (no hard 1px line)
    washes.roundRect(x + 2, y + 2, w - 4, h - 4, r).stroke({ color, alpha: theme.washAlpha * 2, width: 2 });
    washes.roundRect(x + 5, y + 5, w - 10, h - 10, r).stroke({ color, alpha: theme.washAlpha * 0.9, width: 3 });
  }
  staticContent.addChild(washes);

  // ---- 3b. painted section PLOTS: a small section label + a dashed plot border
  //      per area so the six sections read as spatial plots (the foundation for the
  //      editor's area tool). Tinted by the plot's section; drawn once (static). ----
  const plotLayer = new Container();
  plotLayer.label = "plotBorders";
  plotLayer.eventMode = "none";
  // The section LABELS render in their OWN layer added AFTER the props/fence layer (below),
  // so a fence or prop can never cover a section's name. Each label gets a small dark
  // rounded backdrop for legibility against grass/props, and sits INSIDE the plot's top edge.
  const plotLabelLayer = new Container();
  plotLabelLayer.label = "plotLabels";
  plotLabelLayer.eventMode = "none";
  for (const area of mapData.areas) {
    const color = sectionTints[area.section] ?? sectionTints[area.id] ?? 0x8a80c0;
    const x = area.x * ts;
    const y = area.y * ts;
    const w = area.w * ts;
    const h = area.h * ts;
    // a subtle dashed plot border in the section tint (stays UNDER props — a floor marking).
    const border = new Graphics();
    const r = Math.min(10, ts * 0.6);
    border.roundRect(x + 1, y + 1, w - 2, h - 2, r).stroke({ color, alpha: 0.28, width: 1 });
    plotLayer.addChild(border);
    // the section label, pinned INSIDE the plot's top edge, with a backdrop, ABOVE the props.
    const label = new Text({
      text: area.section,
      style: { fontFamily: "ui-monospace, monospace", fontSize: 7, fontWeight: "700", fill: color },
    });
    label.resolution = 3;
    const padX = 2;
    const padY = 1;
    const lx = x + 3;
    const ly = y + 2;
    const backdrop = new Graphics()
      .roundRect(lx - padX, ly - padY, label.width + padX * 2, label.height + padY * 2, 2)
      .fill({ color: 0x05040a, alpha: 0.66 });
    label.position.set(lx, ly);
    label.alpha = 0.95;
    plotLabelLayer.addChild(backdrop);
    plotLabelLayer.addChild(label);
  }
  staticContent.addChild(plotLayer);

  // ---- 4. object shadows + props (graves, terminals, landmarks, decor) ----
  const shadows = new Graphics();
  const props = new Container();

  const addProp = (spriteName: string, tileX: number, tileY: number, glow?: number): void => {
    const tex = frames[`${spriteName}`];
    if (!tex) return;
    // shadow under the object
    drawShadow(shadows, tileX * ts + ts / 2, tileY * ts + ts, tex.width * 0.5, tex.width * 0.2);
    // optional additive glow (crypt-terminal cyan bloom)
    if (glow !== undefined) {
      const bloom = new Graphics()
        .ellipse(tileX * ts + ts / 2, tileY * ts + ts * 0.4, tex.width * 0.7, tex.width * 0.7)
        .fill({ color: glow, alpha: 0.1 });
      bloom.blendMode = "add";
      props.addChild(bloom);
    }
    const s = new Sprite(tex);
    placeAtTile(s, tileX, tileY, ts);
    props.addChild(s);
  };

  // record map destination tiles so decor doesn't overlap them
  for (const g of mapData.graves) occupied.add(`${g.x},${g.y}`);
  for (const w of mapData.workstations) occupied.add(`${w.x},${w.y}`);

  // map-authored destinations first
  for (const grave of mapData.graves) addProp("grave:home", grave.x, grave.y);

  // reactive crypt-terminals: each map workstation is a prop the server can
  // power ON (a working ghost is at it) — tracked by workstation id so `prop.state` can swap
  // it to its active clip + raise its glow IN PLACE (no floor tile mutates).
  const propTerminals = new Map<string, PropTerminal>();
  const idleTex = frames["terminal:idle"];
  const active0 = frames["terminal:active"];
  const active1 = frames["terminal:active1"];
  for (const ws of mapData.workstations) {
    drawShadow(shadows, ws.x * ts + ts / 2, ws.y * ts + ts, (idleTex?.width ?? 16) * 0.5, (idleTex?.width ?? 16) * 0.2);
    const glow = new Graphics()
      .ellipse(ws.x * ts + ts / 2, ws.y * ts + ts * 0.4, (idleTex?.width ?? 16) * 0.7, (idleTex?.width ?? 16) * 0.7)
      .fill({ color: 0x8ff0ff, alpha: 0.1 });
    glow.blendMode = "add";
    props.addChild(glow);
    if (idleTex) {
      const s = new Sprite(idleTex);
      placeAtTile(s, ws.x, ws.y, ts);
      props.addChild(s);
      propTerminals.set(ws.id, {
        sprite: s,
        glow,
        idleFrame: idleTex,
        activeFrames: [active0, active1].filter((t): t is Texture => Boolean(t)),
        frameAcc: 0,
        frameIdx: 0,
      });
    }
  }

  // ---- 4b. the PLACED-PROPS layer: EVERY decor prop — the crypt/mausoleum
  //      landmarks, headstones, trees, the perimeter fence, and the new lanterns/benches/
  //      urns/statues/jack-o'-lanterns/wells/candles — is drawn PURELY from its catalog
  //      def + {tile, orientation, state}. No hard-coded per-prop placement remains; the
  //      SAME server-owned layer feeds collision/A* (footprints) and the editor. ----
  const animatedProps: PropSprite[] = [];
  const placedSprites: PropSprite[] = [];
  for (const placed of mapData.placedProps) {
    const def = catalog.props[placed.catalogId];
    if (!def) continue; // an unknown catalog id is skipped, never a crash
    const ps = new PropSprite({
      def,
      placed: {
        catalogId: placed.catalogId,
        tile: placed.tile,
        orientation: placed.orientation,
        state: placed.state,
      },
      frames,
      book,
      tileSize: ts,
    });
    // a soft grounding drop-shadow under the prop's footprint bottom-center.
    drawShadow(
      shadows,
      placed.tile.x * ts + (def.footprint.w * ts) / 2,
      (placed.tile.y + def.footprint.h) * ts,
      ps.footWidthPx * 0.42,
      ps.footWidthPx * 0.18,
    );
    // a lit prop (lantern/lamp/pumpkin on-state, candle) casts a warm additive glow.
    const lit =
      def.category === "light" &&
      (placed.state === "on" || def.anim !== undefined) &&
      placed.state !== "off";
    if (lit) {
      const bloom = new Graphics()
        .ellipse(ps.sprite.x, ps.sprite.y - ps.sprite.height * 0.5, ps.footWidthPx * 0.8, ps.footWidthPx * 0.8)
        .fill({ color: 0xffcf6b, alpha: 0.12 });
      bloom.blendMode = "add";
      bloom.eventMode = "none";
      props.addChild(bloom);
    }
    placedSprites.push(ps);
    if (ps.animated) animatedProps.push(ps);
  }
  // depth-sort the placed props by their ground baseline so nearer props overlap farther ones.
  placedSprites.sort((a, b) => a.groundY - b.groundY);
  for (const ps of placedSprites) props.addChild(ps.sprite);

  staticContent.addChild(shadows);
  staticContent.addChild(props);
  // section labels LAST so they render ABOVE the fence/prop layer — never covered.
  staticContent.addChild(plotLabelLayer);
  worldRoot.addChild(staticContent);

  // NOTE: the drifting-fog-over-ground TilingSprite is intentionally REMOVED — it made
  // the floor look like it was sliding. Night depth now comes from the STATIC vignette +
  // backdrop; any ambient motion lives OVERHEAD (below), never on the ground.

  const ghostLayer = new Container();
  ghostLayer.label = "ghostLayer";
  worldRoot.addChild(ghostLayer);

  // overhead ambient layer (mist / bats / wisps) — ABOVE the ghosts, alpha-light, the
  // ONLY moving world elements; clearly reads as above the world, never as the floor.
  const overheadLayer = new Container();
  overheadLayer.label = "overheadLayer";
  overheadLayer.eventMode = "none";
  worldRoot.addChild(overheadLayer);

  return { worldRoot, staticContent, ghostLayer, overheadLayer, propTerminals, animatedProps };
}

/**
 * Create + start the render loop. Renders the polished graveyard from `mapData` +
 * `atlas` on a dt-clamped PixiJS ticker reading the Zustand store; recolors ghosts
 * to ANY colour with SHARED clips; wires pointer-drag pan + wheel zoom into the
 * store and a click-to-select hit-test into `onSelectGhost`. Returns a handle whose
 * `destroy()` tears it down.
 */
export async function createRenderLoop(options: RenderLoopOptions): Promise<RenderLoopHandle> {
  const {
    canvas, mapData, atlas, book, paletteBook, catalog, sectionTints,
    onSelectGhost, onPetCritter, onSelectSection, isDepartmentSection,
  } = options;
  const theme = options.theme ?? DEFAULT_WORLD_THEME;

  // nearest scaling globally — crisp pixels, never smoothed
  TextureSource.defaultOptions.scaleMode = "nearest";

  const reduceMotion =
    typeof window !== "undefined" && typeof window.matchMedia === "function"
      ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
      : false;

  const app = new Application();
  await app.init({
    canvas,
    background: options.background ?? theme.background,
    antialias: false,
    resizeTo: canvas.parentElement ?? window,
    autoDensity: true,
    resolution: window.devicePixelRatio || 1,
  });

  const base = atlasToTexture(atlas);
  const frames = sliceFrames(atlas, base);

  // the STATIC night sky backdrop sits BEHIND the world (added to the stage first).
  let backdrop = buildBackdrop(frames, theme, app.screen.width, app.screen.height);
  app.stage.addChild(backdrop);

  // `currentMap` + the mutable static/propTerminals/animatedProps refs let the editor's
  // reloadMap swap the world in place (no Application re-init). Dimensions never change in the
  // editor, so camera bounds + overhead sizing (built once below) stay valid across a reload.
  let currentMap = mapData;
  // eslint-disable-next-line prefer-const
  let { worldRoot, staticContent, ghostLayer, overheadLayer, propTerminals, animatedProps } =
    buildWorld(mapData, frames, sectionTints, theme, catalog, book);
  app.stage.addChild(worldRoot);

  // Graveyard Builder overlay — drawn on top of the world when the editor is active.
  const editorOverlay = new EditorOverlay();
  worldRoot.addChild(editorOverlay.container);

  // mission/section link lines: a marching-ants dashed spectral line from each
  // section anchor to its member ghosts. Batched into ONE Graphics, redrawn per frame, in an
  // overhead layer above the ghosts (motion here never touches the static floor).
  const linkG = new Graphics();
  linkG.label = "linkLines";
  linkG.eventMode = "none";
  worldRoot.addChild(linkG);
  let linkT = 0;

  // bubbles draw ABOVE the ghosts (server say/overlay), in world space so they pan/zoom.
  const bubbleLayer = new Container();
  bubbleLayer.label = "bubbleLayer";
  worldRoot.addChild(bubbleLayer);
  interface BubbleRender {
    container: Container;
    createdMs: number;
    ageMs: number;
  }
  const bubbleSprites = new Map<string, BubbleRender>();

  // 🔊 sound-cue pings: a small speaker glyph that rises + fades over the ghost that
  // triggered a sound. Drawn in world space above the ghosts; purely cosmetic, cleared on fade.
  const pingLayer = new Container();
  pingLayer.label = "soundPingLayer";
  pingLayer.eventMode = "none";
  worldRoot.addChild(pingLayer);
  interface PingRender {
    text: Text;
    createdMs: number;
  }
  const pingSprites = new Map<string, PingRender>();
  const PING_TTL_MS = 900;

  // overhead ambient (drifting mist + bats/wisps) — the ONLY moving world elements,
  // above the ghosts. Built once; a still camera shows a still floor.
  const worldWpx = mapData.width * mapData.tileSize;
  const worldHpx = mapData.height * mapData.tileSize;
  const overhead = buildOverhead(overheadLayer, frames, worldWpx, worldHpx);
  addFlyers(overhead, overheadLayer, frames, worldHpx);

  // edge vignette on the STAGE (screen space — does not pan/zoom). STATIC night depth.
  let vignette = new Sprite(makeVignetteTexture(app.screen.width, app.screen.height, theme));
  vignette.eventMode = "none";
  app.stage.addChild(vignette);
  let vigW = app.screen.width;
  let vigH = app.screen.height;

  // the ATTENTION red edge-vignette sits ABOVE the night vignette; its alpha throbs
  // when any ghost needs the operator and returns to 0 when the condition clears.
  let attnVignette = new Sprite(makeAttentionVignetteTexture(app.screen.width, app.screen.height));
  attnVignette.eventMode = "none";
  attnVignette.alpha = 0;
  app.stage.addChild(attnVignette);
  let attnT = 0;
  // attention camera-refocus: snap on none→some, then re-nudge every ~5s while any remains.
  let attnRefocusAcc = 0;
  const ATTN_REFOCUS_MS = 5000;
  let hadAttention = false;

  // per-ghost arbitrary-colour frame factory (shared clips, recolored pixels)
  const ghostFactory = new GhostFrameFactory(atlas, frames, paletteBook);
  const resolveFrames: FrameResolver = (ghost) => ghostFactory.framesFor(ghost);

  // seed the camera bounds from the world size (centre may roam the world rect)
  useWorldStore.getState().setCameraBounds({
    minX: 0,
    maxX: mapData.width * mapData.tileSize,
    minY: 0,
    maxY: mapData.height * mapData.tileSize,
  });

  const sprites = new Map<string, GhostSprite>();
  // autonomous graveyard critters: cats live on the ghost layer (depth-sorted), the
  // wisp/bat flyers on the overhead layer. Server-authoritative positions; petting flashes a heart.
  const critterSprites = new Map<string, CritterSprite>();

  // ---- spawn/dissolve flourish: a fog-condense materialize on a new ghost + a
  //      spectral scatter dissolve on removal. Reduced motion → a quick alpha fade (no motes). ----
  const flourishLayer = new Container();
  flourishLayer.label = "flourishLayer";
  flourishLayer.eventMode = "none";
  worldRoot.addChild(flourishLayer);
  interface RunningFlourish {
    effect: FlourishEffect;
  }
  const flourishes: RunningFlourish[] = [];
  // per-ghost grave-rest presence (196): the ghost container alpha ramps IN on spawn/depart
  // (materialize, rising) and OUT into the home gravestone on grave-rest (de-materialize,
  // sinking→sunk). One writer of `gs.container.alpha` for both spawn fade-in AND rest sink.
  const presence = new Map<string, PresenceState>();
  // graceful despawn (196 FIX 1): a ghost removed from the store does NOT pop out the same
  // frame — its sprite is kept alive for one FLOURISH_MS window while `gs.container.alpha`
  // ramps 1→0 (the existing dissolve body curve `ghostAlphaFor("dissolve")`), in lockstep with
  // the scatter motes, THEN destroyed. Reduced motion skips the ramp (destroy next frame).
  interface Despawning {
    gs: GhostSprite;
    elapsed: number;
    startAlpha: number;
  }
  const despawning = new Map<string, Despawning>();

  const spawnFlourish = (kind: "materialize" | "dissolve", x: number, y: number, seed: number): void => {
    if (reduceMotion) return; // reduced motion: no motes (alpha fade only, handled per-ghost)
    const effect = makeFlourish(kind, seed);
    effect.container.position.set(x, y);
    flourishLayer.addChild(effect.container);
    flourishes.push({ effect });
  };

  // ---- camera follow: smoothly track the selected ghost until the user pans/zooms ----
  // `followTargetId` is armed when the selection changes to a ghost and cleared by any
  // manual pan/pinch (below) so following never fights the operator.
  let followTargetId: string | null = null;
  let lastSelectedSeen: string | null = null;
  // number of active pointers on the canvas (set by the input handlers) — no follow while touching.
  let activePointerCount = 0;
  // smoothed target velocity (world px/ms) for the follow look-ahead lead.
  let followPrevX: number | null = null;
  let followPrevY: number | null = null;
  let followVX = 0;
  let followVY = 0;
  const FOLLOW_LOOKAHEAD_MS = 260; // lead the camera ahead of the target by ~a quarter second

  // ---- the dt-clamped ticker: the ONLY per-frame work; no React re-render ----
  const tick = (): void => {
    const dt = Math.min(app.ticker.deltaMS, MAX_DT_MS);
    const state = useWorldStore.getState();

    // arm follow when the selection newly points at a ghost (e.g. click / roster select)
    if (state.selectedGhostId !== lastSelectedSeen) {
      lastSelectedSeen = state.selectedGhostId;
      if (state.selectedGhostId) followTargetId = state.selectedGhostId;
    }
    // smooth follow-lerp toward the selected ghost + a velocity look-ahead lead (only when
    // enabled and the user isn't actively touching, so following never fights the operator).
    if (followTargetId && state.followEnabled && activePointerCount === 0) {
      const target = state.ghosts[followTargetId];
      const pos = target?.position;
      if (pos) {
        // smoothed target velocity → a lead point ahead of the ghost's travel.
        if (followPrevX !== null && followPrevY !== null && dt > 0) {
          const instVX = (pos.x - followPrevX) / dt;
          const instVY = (pos.y - followPrevY) / dt;
          followVX += (instVX - followVX) * 0.2;
          followVY += (instVY - followVY) * 0.2;
        }
        followPrevX = pos.x;
        followPrevY = pos.y;
        const lead = leadPoint(pos.x, pos.y, followVX, followVY, FOLLOW_LOOKAHEAD_MS);
        state.followTo(lead.x, lead.y, Math.min(1, dt * 0.006));
      } else if (!target) {
        followTargetId = null; // the followed ghost went away
        followPrevX = null;
        followPrevY = null;
        followVX = 0;
        followVY = 0;
      }
    }

    // camera transform on the world root (world-space centre -> screen centre)
    const cam = state.camera;
    worldRoot.scale.set(cam.zoom);
    worldRoot.position.set(
      app.screen.width / 2 - cam.x * cam.zoom,
      app.screen.height / 2 - cam.y * cam.zoom,
    );

    // overhead ambient drift (mist/bats/wisps) — the ONLY per-frame world motion,
    // above the ghosts, disabled entirely under prefers-reduced-motion.
    overhead.update(dt, reduceMotion);

    // editor overlay: draw the grid + draft props + preview + selection when active.
    if (options.editor) editorOverlay.update(options.editor.getView());

    // placed-prop flicker: lanterns/candles cycle their clip IN PLACE (the prop
    // never moves, no floor tile mutates — static-floor invariant preserved).
    for (const ps of animatedProps) ps.update(dt, reduceMotion);

    // ---- ATTENTION world FX: a red edge-vignette throb + a camera refocus while any
    //      ghost needs the operator; both clear the instant the condition resolves. ----
    attnT += dt;
    let firstAttentionPos: { x: number; y: number } | null = null;
    for (const g of Object.values(state.ghosts)) {
      if (g.attention?.needs) {
        if (g.position) {
          firstAttentionPos = { x: g.position.x, y: g.position.y };
          break;
        }
      }
    }
    const anyAttention = firstAttentionPos !== null || hasAttention(state.ghosts);
    // vignette alpha: throb when needed (steady low under reduced motion), else ease back to 0.
    if (anyAttention) {
      const target = reduceMotion ? 0.26 : 0.14 + 0.22 * (0.5 + 0.5 * Math.sin(attnT * 0.005));
      attnVignette.alpha += (target - attnVignette.alpha) * Math.min(1, dt * 0.01);
    } else {
      attnVignette.alpha += (0 - attnVignette.alpha) * Math.min(1, dt * 0.01);
      if (attnVignette.alpha < 0.01) attnVignette.alpha = 0;
    }
    // camera refocus: snap on none→some, then re-nudge every ~5s while any remains (never while
    // the operator is actively touching, so it never fights a manual pan).
    if (anyAttention && firstAttentionPos && activePointerCount === 0) {
      if (!hadAttention) {
        state.setCamera({ x: firstAttentionPos.x, y: firstAttentionPos.y });
        attnRefocusAcc = 0;
      } else {
        attnRefocusAcc += dt;
        if (attnRefocusAcc >= ATTN_REFOCUS_MS) {
          attnRefocusAcc = 0;
          state.followTo(firstAttentionPos.x, firstAttentionPos.y, 0.5);
        }
      }
    }
    hadAttention = anyAttention;

    // keep the STATIC vignette + night backdrop covering the screen (rebuild on a real resize)
    if (app.screen.width !== vigW || app.screen.height !== vigH) {
      vigW = app.screen.width;
      vigH = app.screen.height;
      const oldVig = vignette;
      vignette = new Sprite(makeVignetteTexture(vigW, vigH, theme));
      vignette.eventMode = "none";
      app.stage.addChild(vignette);
      oldVig.destroy();
      // rebuild the attention vignette at the new size (preserve its current alpha).
      const oldAttn = attnVignette;
      attnVignette = new Sprite(makeAttentionVignetteTexture(vigW, vigH));
      attnVignette.eventMode = "none";
      attnVignette.alpha = oldAttn.alpha;
      app.stage.addChild(attnVignette);
      oldAttn.destroy();
      const oldBackdrop = backdrop;
      backdrop = buildBackdrop(frames, theme, vigW, vigH);
      app.stage.addChildAt(backdrop, 0); // keep the sky behind the world
      oldBackdrop.destroy({ children: true });
    }
    vignette.width = app.screen.width;
    vignette.height = app.screen.height;
    attnVignette.width = app.screen.width;
    attnVignette.height = app.screen.height;

    // sync ghosts: add new, update present, drop removed.
    //
    // Reduced-motion contract, enforced via the `reduceMotion` flag threaded into
    // `gs.update` below: the ghost's CORE motion — its continuous idle-wander position (the
    // `state === "wander"` position stream) and its walk-cycle clip — ALWAYS flows,
    // because a frozen ghost would not read as alive. Only the DECORATIVE embellishment layered
    // on top is dropped under `prefers-reduced-motion`: the stationary float-bob + overlay bob
    // (GhostSprite), the spawn/dissolve motes (`spawnFlourish` early-returns), the overhead
    // mist/bats/wisps drift (`overhead.update` no-ops), and the attention vignette throb (held
    // steady). So reduced-motion ghosts keep moving; nothing merely ornamental animates.
    const ghosts = state.ghosts;
    const selectedId = state.selectedGhostId;
    const showLabels = state.showLabels;
    for (const [id, ghost] of Object.entries(ghosts)) {
      let gs = sprites.get(id);
      if (!gs) {
        // reclaim a mid-despawn sprite if the ghost reappears before its fade completes (196
        // FIX 1) — avoids a destroy/recreate flicker and a duplicate sprite.
        const reviving = despawning.get(id);
        if (reviving) {
          gs = reviving.gs;
          despawning.delete(id);
          sprites.set(id, gs);
          presence.set(id, initialPresence(ghost.state, reduceMotion));
          gs.container.alpha = presence.get(id)!.alpha;
        } else {
        gs = new GhostSprite({ resolveFrames, book });
        ghostLayer.addChild(gs.container);
        sprites.set(id, gs);
        // seed this ghost's presence from its state: an ACTIVE ghost materializes into the
        // world (rising fade-in + fog flourish); one first seen already at its home grave
        // starts SUNK in the stone (no flourish — it's resting, not arriving) (196).
        const initial = initialPresence(ghost.state, reduceMotion);
        presence.set(id, initial);
        gs.container.alpha = initial.alpha;
        if (!isGraveRestState(ghost.state)) {
          const pos = ghost.position;
          if (pos) spawnFlourish("materialize", pos.x, pos.y, hashId(id));
        }
        }
      }
      // suppress the float-bob while at grave-rest (IDLE) so a resting ghost sits IN the
      // gravestone, never hovering above it.
      gs.update(ghost, dt, {
        selected: id === selectedId,
        showLabel: showLabels,
        reduceMotion,
        suppressBob: isGraveRestState(ghost.state),
      });
      // grave-rest presence (196): sink INTO / rise OUT of the home gravestone on the
      // IDLE<->active transition; a fog flourish fires at the grave on each transition and
      // the body alpha ramps down (de-materialize) / up (materialize). This is the SOLE
      // writer of `gs.container.alpha` — it also carries the spawn fade-in (rising phase).
      const prev = presence.get(id) ?? initialPresence(ghost.state, reduceMotion);
      const step = stepPresence(prev, isGraveRestState(ghost.state), dt, reduceMotion);
      presence.set(id, step.state);
      gs.container.alpha = step.state.alpha;
      if (step.flourish && ghost.position) {
        spawnFlourish(step.flourish, ghost.position.x, ghost.position.y, hashId(id));
      }
    }
    for (const [id, gs] of sprites) {
      if (!ghosts[id]) {
        // a removed ghost dissolves: a spectral scatter flourish at its last position AND a
        // graceful body fade-out (196 FIX 1) — the sprite is handed to `despawning` so its
        // alpha ramps 1→0 over FLOURISH_MS alongside the motes, instead of popping the same
        // frame. Under reduced motion (no motes) it still fades via the ramp below.
        const box = gs.hitBox();
        if (box) spawnFlourish("dissolve", box.x + box.w / 2, box.y + box.h, hashId(id));
        sprites.delete(id);
        presence.delete(id);
        despawning.set(id, { gs, elapsed: 0, startAlpha: gs.container.alpha });
      }
    }
    // advance graceful despawns (196 FIX 1): ramp the fading body alpha down using the shared
    // dissolve curve, then destroy the sprite once the window elapses. Mirrors the spawn feel
    // (FLOURISH_MS). Reduced motion collapses the window to an immediate destroy.
    for (const [id, dsp] of despawning) {
      dsp.elapsed += dt;
      const t = reduceMotion ? 1 : Math.min(1, dsp.elapsed / FLOURISH_MS);
      dsp.gs.container.alpha = dsp.startAlpha * ghostAlphaFor("dissolve", t);
      if (t >= 1) {
        dsp.gs.destroy();
        despawning.delete(id);
      }
    }

    // advance + reap flourishes (motes converging/scattering; done → destroy).
    for (let i = flourishes.length - 1; i >= 0; i--) {
      if (flourishes[i].effect.update(dt)) {
        flourishes[i].effect.container.destroy({ children: true });
        flourishes.splice(i, 1);
      }
    }
    // ---- reactive props: power ON each crypt-terminal a working ghost is at
    //      (swap to its active clip + raise its glow), settle the rest — IN PLACE, no floor. ----
    const activeProps = state.activeProps;
    for (const [id, term] of propTerminals) {
      const on = activeProps[id] === true;
      if (on && term.activeFrames.length > 0) {
        term.frameAcc += dt;
        if (term.frameAcc >= 240) {
          term.frameAcc -= 240;
          term.frameIdx = (term.frameIdx + 1) % term.activeFrames.length;
        }
        term.sprite.texture = term.activeFrames[term.frameIdx];
        term.glow.alpha += (0.42 - term.glow.alpha) * Math.min(1, dt * 0.006);
      } else {
        if (term.sprite.texture !== term.idleFrame) term.sprite.texture = term.idleFrame;
        term.frameIdx = 0;
        term.frameAcc = 0;
        term.glow.alpha += (0.1 - term.glow.alpha) * Math.min(1, dt * 0.006);
      }
    }

    // ---- sync critters: cats on the ghost layer (depth-sorted with ghosts), the
    //      wisp/bat flyers on the overhead layer; pet flashes a heart/spark. ----
    const critters = state.critters;
    const pets = state.critterPets;
    for (const [id, critter] of Object.entries(critters)) {
      let cs = critterSprites.get(id);
      if (!cs) {
        cs = new CritterSprite(critter.kind, frames, book);
        (critter.layer === "overhead" ? overheadLayer : ghostLayer).addChild(cs.container);
        critterSprites.set(id, cs);
      }
      cs.update(critter, dt, { reduceMotion }, pets[id]);
      // a pet flash whose flourish has fully elapsed is cleared from the store.
      if (pets[id] !== undefined && Date.now() - pets[id] > 1000) {
        useWorldStore.getState().clearCritterPet(id);
      }
    }
    for (const [id, cs] of critterSprites) {
      if (!critters[id]) {
        cs.destroy();
        critterSprites.delete(id);
      }
    }

    // depth-sort ghosts by ground Y so nearer ghosts overlap farther ones
    ghostLayer.children.sort((a, b) => a.y - b.y);

    // ---- mission/section link lines (P12): dashed spectral lines from each section anchor to
    //      its member (working/attention) ghosts; marching under motion, static when reduced. ----
    linkT += dt;
    linkG.clear();
    const ts = currentMap.tileSize;
    const offset = reduceMotion ? 0 : dashOffset(linkT, 0.03, 8);
    for (const ghost of Object.values(ghosts)) {
      const sec = ghost.section;
      const pos = ghost.position;
      if (!sec || !pos) continue;
      // only link ACTIVE ghosts (working / needing attention) so idle wanderers aren't roped in.
      const active = ghost.state !== "IDLE" && ghost.state !== "RETURNING_HOME" && ghost.state !== "WALKING";
      if (!active && !ghost.attention?.needs) continue;
      const region = currentMap.regions[sec];
      if (!region) continue;
      const ax = (region.x + region.w / 2) * ts;
      const ay = (region.y + region.h / 2) * ts;
      drawLinkLine(linkG, ax, ay, pos.x, pos.y, offset, {
        color: sectionTints[sec] ?? 0x8ff0ff,
        alpha: 0.42,
        dash: 4,
        gap: 4,
        width: 1,
      });
    }

    // ---- speech/thought bubbles: draw the server say/overlay, fade after ttl ----
    const bubbles = state.bubbles;
    for (const [gid, bubble] of Object.entries(bubbles)) {
      const ghost = ghosts[gid];
      let bs = bubbleSprites.get(gid);
      // a newer bubble for the same ghost replaces the old sprite.
      if (bs && bs.createdMs !== bubble.createdMs) {
        bs.container.destroy();
        bubbleSprites.delete(gid);
        bs = undefined;
      }
      if (!bs) {
        const container = makeBubbleContainer(bubble.text, bubble.kind);
        bs = { container, createdMs: bubble.createdMs, ageMs: 0 };
        bubbleLayer.addChild(container);
        bubbleSprites.set(gid, bs);
      }
      bs.ageMs += dt;
      if (bs.ageMs >= bubble.ttlMs || !ghost) {
        bs.container.destroy();
        bubbleSprites.delete(gid);
        useWorldStore.getState().clearBubble(gid);
        continue;
      }
      // anchor just above the ghost's head; a gentle rise unless reduced motion is preferred.
      const boxH = sprites.get(gid)?.hitBox()?.h ?? 24;
      const gx = ghost.position?.x ?? 0;
      const gy = ghost.position?.y ?? 0;
      const rise = reduceMotion ? 0 : -Math.min(6, bs.ageMs * 0.01);
      bs.container.position.set(gx, gy - boxH - 6 + rise);
      const t = bs.ageMs / bubble.ttlMs;
      bs.container.alpha = t < 0.65 ? 1 : Math.max(0, 1 - (t - 0.65) / 0.35);
    }
    // drop bubble sprites whose store entry vanished (ghost removed / cleared elsewhere).
    for (const [gid, bs] of bubbleSprites) {
      if (!bubbles[gid]) {
        bs.container.destroy();
        bubbleSprites.delete(gid);
      }
    }

    // ---- 🔊 sound-cue pings: a small speaker glyph rising + fading over the triggering ghost ----
    const pings = state.soundPings;
    const nowMs = Date.now();
    for (const [gid, createdMs] of Object.entries(pings)) {
      const ghost = ghosts[gid];
      let ps = pingSprites.get(gid);
      if (ps && ps.createdMs !== createdMs) {
        ps.text.destroy();
        pingSprites.delete(gid);
        ps = undefined;
      }
      const age = nowMs - createdMs;
      if (age >= PING_TTL_MS || !ghost) {
        if (ps) {
          ps.text.destroy();
          pingSprites.delete(gid);
        }
        useWorldStore.getState().clearSoundPing(gid);
        continue;
      }
      if (!ps) {
        const text = new Text({
          text: "🔊",
          style: { fontFamily: "ui-monospace, monospace", fontSize: 12 },
        });
        text.resolution = 2;
        text.anchor.set(0.5, 1);
        text.eventMode = "none";
        ps = { text, createdMs };
        pingLayer.addChild(text);
        pingSprites.set(gid, ps);
      }
      const boxH = sprites.get(gid)?.hitBox()?.h ?? 24;
      const t = age / PING_TTL_MS;
      const rise = reduceMotion ? 8 : 8 + t * 12;
      ps.text.position.set(ghost.position?.x ?? 0, (ghost.position?.y ?? 0) - boxH - 8 - rise);
      ps.text.alpha = t < 0.5 ? 1 : Math.max(0, 1 - (t - 0.5) / 0.5);
    }
    for (const [gid, ps] of pingSprites) {
      if (pings[gid] === undefined) {
        ps.text.destroy();
        pingSprites.delete(gid);
      }
    }
  };
  app.ticker.add(tick);

  // ---- input: 1-finger/mouse drag pan + 2-finger pinch-zoom + wheel-at-cursor +
  //      double-click pivot-zoom -> the store; a tap/click (under threshold) -> ghost select ----
  const pointers = new Map<number, PointerPos>();
  let primaryId: number | null = null; // the pointer that owns single-finger pan / click
  let lastX = 0;
  let lastY = 0;
  let downX = 0; // primary pointer-down origin (client) for click detection
  let downY = 0;
  let downButton = 0; // primary pointer-down mouse button (0=left, 2=right) for editor erase
  let moved = false; // primary moved past the click threshold → a pan, not a select
  let gestureWasMulti = false; // this touch-sequence used ≥2 fingers → never a click
  let pinchPrevDist = 0;
  let pinchPrevMid: PointerPos = { x: 0, y: 0 };

  /** Map a screen (client) point to world coords using the ticker's camera transform. */
  const clientToWorld = (clientX: number, clientY: number): { x: number; y: number } => {
    const rect = canvas.getBoundingClientRect();
    const localX = clientX - rect.left;
    const localY = clientY - rect.top;
    return {
      x: (localX - worldRoot.position.x) / (worldRoot.scale.x || 1),
      y: (localY - worldRoot.position.y) / (worldRoot.scale.y || 1),
    };
  };

  const twoPointers = (): [PointerPos, PointerPos] => {
    const it = pointers.values();
    return [it.next().value as PointerPos, it.next().value as PointerPos];
  };

  /** The section id of the labelled area under a WORLD point, or null on bare ground (196). The
   *  live `currentMap.areas` are tile-space rects each carrying a `section` — the same plots the
   *  render loop fills, so a department's painted zone is exactly its clickable target. */
  const sectionAtWorld = (worldX: number, worldY: number): string | null => {
    const t = tileFromWorld(worldX, worldY, currentMap.tileSize);
    for (const area of currentMap.areas) {
      if (t.x >= area.x && t.x < area.x + area.w && t.y >= area.y && t.y < area.y + area.h) {
        return area.section;
      }
    }
    return null;
  };

  const onPointerDown = (e: PointerEvent): void => {
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    activePointerCount = pointers.size;
    canvas.setPointerCapture?.(e.pointerId);
    // while a pointer is down (pan/gesture), show the grabbing cursor; the next hover recomputes
    // the pointer/grab affordance (S1). Inline style, so it overrides the CSS :active rule too.
    if (isDepartmentSection) canvas.style.cursor = "grabbing";
    if (pointers.size === 1) {
      primaryId = e.pointerId;
      lastX = e.clientX;
      lastY = e.clientY;
      downX = e.clientX;
      downY = e.clientY;
      downButton = e.button;
      moved = false;
      gestureWasMulti = false;
    } else if (pointers.size === 2) {
      gestureWasMulti = true;
      followTargetId = null; // a manual gesture cancels follow
      const [p1, p2] = twoPointers();
      pinchPrevDist = pointerDistance(p1, p2);
      pinchPrevMid = pointerMidpoint(p1, p2);
    }
  };

  const onPointerMove = (e: PointerEvent): void => {
    // editor hover (no button down): drive the held-prop placement preview under the cursor.
    if (options.editor && pointers.size === 0 && options.editor.getView().active) {
      const w = clientToWorld(e.clientX, e.clientY);
      const t = tileFromWorld(w.x, w.y, currentMap.tileSize);
      options.editor.onHover(t.x, t.y);
      return;
    }
    // S1 hover affordance: with no button down, swap the stage cursor to `pointer` over a
    // clickable department plot and `grab` over non-department ground (reuses the sectionAtWorld
    // hit-test — the same plots the click gate resolves). Only on true hover (no active pointer),
    // so an in-flight drag keeps its `grabbing` cursor.
    if (pointers.size === 0) {
      if (isDepartmentSection) {
        const w = clientToWorld(e.clientX, e.clientY);
        canvas.style.cursor = isDepartmentSection(sectionAtWorld(w.x, w.y)) ? "pointer" : "grab";
      }
      return;
    }
    const p = pointers.get(e.pointerId);
    if (!p) return;
    p.x = e.clientX;
    p.y = e.clientY;
    const store = useWorldStore.getState();
    if (pointers.size >= 2) {
      // two-finger pinch-zoom around the midpoint + two-finger pan by the midpoint travel.
      const [p1, p2] = twoPointers();
      const curDist = pointerDistance(p1, p2);
      const curMid = pointerMidpoint(p1, p2);
      const scale = pinchScale(pinchPrevDist, curDist);
      if (scale !== 1) {
        const w = clientToWorld(curMid.x, curMid.y);
        store.zoomCameraAt(scale, w.x, w.y);
      }
      store.panCamera(curMid.x - pinchPrevMid.x, curMid.y - pinchPrevMid.y);
      pinchPrevDist = curDist;
      pinchPrevMid = curMid;
      followTargetId = null;
    } else if (pointers.size === 1 && e.pointerId === primaryId) {
      store.panCamera(e.clientX - lastX, e.clientY - lastY);
      lastX = e.clientX;
      lastY = e.clientY;
      if (!isClick(e.clientX - downX, e.clientY - downY)) {
        moved = true;
        followTargetId = null; // an intentional pan takes over from follow
      }
    }
  };

  const endPointer = (e: PointerEvent, select: boolean): void => {
    const wasPrimaryTap = e.pointerId === primaryId && !moved && !gestureWasMulti;
    pointers.delete(e.pointerId);
    activePointerCount = pointers.size;
    canvas.releasePointerCapture?.(e.pointerId);
    // a tap/click (not a pan, never part of a pinch) hit-tests the ghost sprites → select the
    // top-most under the point. A miss passes null (deselect / no-op). Listeners
    // live on the canvas, so a tap that started over a DOM HUD overlay never reaches here.
    if (select && wasPrimaryTap && options.editor && options.editor.getView().active) {
      // Editor mode: a tap applies the active tool at the tile (left) or quick-erases (right).
      const world = clientToWorld(e.clientX, e.clientY);
      const t = tileFromWorld(world.x, world.y, currentMap.tileSize);
      options.editor.onTile(t.x, t.y, downButton === 2 ? "right" : "left");
    } else if (select && wasPrimaryTap) {
      const world = clientToWorld(e.clientX, e.clientY);
      // critters are the foreground toy: a tap over one PETS it and never falls through to a
      // ghost select. Only when no critter is hit do we hit-test the ghost sprites.
      let pettedCritter: string | null = null;
      if (onPetCritter) {
        const centities: HitEntity[] = [];
        for (const [id, cs] of critterSprites) {
          const box = cs.hitBox();
          if (box) centities.push({ id, box, depth: box.y + box.h });
        }
        pettedCritter = topmostHit(world.x, world.y, centities);
      }
      if (pettedCritter && onPetCritter) {
        onPetCritter(pettedCritter);
      } else {
        // hit-test the ghost sprites first (selection); on a MISS fall through to a department
        // area (196: click a department plot to open its found results). Ghost + department are
        // mutually-exclusive focuses — a ghost hit dismisses any open department card, and empty
        // ground clears both.
        const entities: HitEntity[] = [];
        for (const [id, gs] of sprites) {
          const box = gs.hitBox();
          if (box) entities.push({ id, box, depth: box.y + box.h });
        }
        const ghostHit = onSelectGhost ? topmostHit(world.x, world.y, entities) : null;
        if (ghostHit) {
          onSelectGhost?.(ghostHit);
          onSelectSection?.(null);
        } else {
          onSelectGhost?.(null); // a ghost miss deselects the ghost (unchanged behaviour)
          if (onSelectSection) onSelectSection(sectionAtWorld(world.x, world.y));
        }
      }
    }
    if (pointers.size === 1) {
      // dropped from a pinch to one finger — keep panning with the remaining finger, no select.
      const [remId, rem] = [...pointers.entries()][0];
      primaryId = remId;
      lastX = rem.x;
      lastY = rem.y;
      downX = rem.x;
      downY = rem.y;
      // gestureWasMulti stays true so lifting the last finger doesn't select.
    } else if (pointers.size === 0) {
      primaryId = null;
    }
  };

  const onPointerUp = (e: PointerEvent): void => endPointer(e, true);
  const onPointerCancel = (e: PointerEvent): void => endPointer(e, false);
  const onPointerLeave = (e: PointerEvent): void => {
    if (pointers.has(e.pointerId)) endPointer(e, false);
  };

  const onWheel = (e: WheelEvent): void => {
    e.preventDefault();
    // a smooth zoom accumulator toward the cursor; ctrl-wheel (trackpad pinch) zooms harder.
    const factor = Math.pow(1.0016, -e.deltaY * (e.ctrlKey ? 2.2 : 1));
    const w = clientToWorld(e.clientX, e.clientY);
    followTargetId = null;
    useWorldStore.getState().zoomCameraAt(factor, w.x, w.y);
  };

  const onDoubleClick = (e: MouseEvent): void => {
    e.preventDefault();
    const w = clientToWorld(e.clientX, e.clientY);
    followTargetId = null;
    useWorldStore.getState().zoomCameraAt(1.7, w.x, w.y);
  };

  // suppress the browser context menu on the canvas so right-click erase works in the editor.
  const onContextMenu = (e: MouseEvent): void => {
    if (options.editor && options.editor.getView().active) e.preventDefault();
  };

  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerCancel);
  canvas.addEventListener("pointerleave", onPointerLeave);
  canvas.addEventListener("wheel", onWheel, { passive: false });
  canvas.addEventListener("dblclick", onDoubleClick);
  canvas.addEventListener("contextmenu", onContextMenu);

  // Swap the live world in place after a validated map.save/reset — rebuild ONLY the
  // static content container (ground/props/plots), reusing the same Application + ghost/overhead
  // layers (re-initialising Pixi on the same canvas wedges headless WebGL). Dimensions are
  // assumed unchanged (the editor has no resize tool), so camera bounds/overhead stay valid.
  const reloadMap = (newMap: WorldMapData): void => {
    const built = buildWorld(newMap, frames, sectionTints, theme, catalog, book);
    // steal the freshly-built static content; discard the throwaway root + its unused layers.
    built.worldRoot.removeChild(built.staticContent);
    built.worldRoot.destroy({ children: true });
    worldRoot.removeChild(staticContent);
    staticContent.destroy({ children: true });
    staticContent = built.staticContent;
    worldRoot.addChildAt(staticContent, 0); // keep it beneath ghosts/overhead/overlay
    propTerminals = built.propTerminals;
    animatedProps = built.animatedProps;
    currentMap = newMap;
  };

  const destroy = (): void => {
    app.ticker.remove(tick);
    canvas.removeEventListener("contextmenu", onContextMenu);
    editorOverlay.destroy();
    canvas.removeEventListener("pointerdown", onPointerDown);
    canvas.removeEventListener("pointermove", onPointerMove);
    canvas.removeEventListener("pointerup", onPointerUp);
    canvas.removeEventListener("pointercancel", onPointerCancel);
    canvas.removeEventListener("pointerleave", onPointerLeave);
    canvas.removeEventListener("wheel", onWheel);
    canvas.removeEventListener("dblclick", onDoubleClick);
    for (const gs of sprites.values()) gs.destroy();
    sprites.clear();
    for (const dsp of despawning.values()) dsp.gs.destroy();
    despawning.clear();
    for (const cs of critterSprites.values()) cs.destroy();
    critterSprites.clear();
    for (const bs of bubbleSprites.values()) bs.container.destroy();
    bubbleSprites.clear();
    for (const ps of pingSprites.values()) ps.text.destroy();
    pingSprites.clear();
    for (const f of flourishes) f.effect.container.destroy({ children: true });
    flourishes.length = 0;
    ghostFactory.destroy();
    app.destroy(false, { children: true });
  };

  return { app, reloadMap, destroy };
}
