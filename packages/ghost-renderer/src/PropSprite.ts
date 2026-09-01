// ghostopia ghost-renderer — a placed-prop sprite driven PURELY by the catalog.
//
// A PropSprite draws ONE placed prop from its catalog def + {tile, orientation, state} —
// nothing about the prop is hard-coded here: the manifest region comes from
// `resolvePropRegion(def, orientation, state)`, the texture from the atlas, the footprint
// from the def (bottom-center anchored over its tile box), and any flicker from the def's
// anim clip. This is the renderer half of the ONE system the Graveyard Builder edits.
//
// Imports only PixiJS + pure @ghostopia/ghost-art helpers — NO GhostCrawl SDK, NO backend.

import { Sprite, Texture } from "pixi.js";
import { resolvePropRegion, type PropDef, type AnimationBook } from "@ghostopia/ghost-art";

/** A placed prop: which catalog prop, where (top-left tile of its footprint), facing, state. */
export interface PlacedProp {
  catalogId: string;
  tile: { x: number; y: number };
  orientation: string;
  state?: string | null;
}

export interface PropSpriteOptions {
  def: PropDef;
  placed: PlacedProp;
  /** atlas textures keyed `"sprite:region"`. */
  frames: Record<string, Texture>;
  book: AnimationBook;
  tileSize: number;
}

/** Resolve the atlas texture for a prop def's region (or undefined if not packed). */
function texFor(
  frames: Record<string, Texture>,
  sprite: string,
  region: string,
): Texture | undefined {
  return frames[`${sprite}:${region}`];
}

/**
 * A placed-prop sprite. Positioned at the bottom-center of its footprint box so it grounds
 * on the tile it sits on (matching the ghost/decor anchor convention). `groundY` is its
 * baseline for depth-sorting against other props. A prop whose def declares an `anim` clip
 * flickers (lantern/candle) via {@link update}; a static prop's `update` is a no-op.
 */
export class PropSprite {
  readonly sprite: Sprite;
  /** the world-space baseline Y used to depth-sort props (nearer props draw in front). */
  readonly groundY: number;
  /** the prop's footprint width in pixels (for the drop-shadow radius). */
  readonly footWidthPx: number;

  private readonly clip: Texture[] = [];
  private readonly frameMs: number[] = [];
  private frameAcc = 0;
  private frameIdx = 0;

  constructor(opts: PropSpriteOptions) {
    const { def, placed, frames, book, tileSize } = opts;
    const { region, mirror } = resolvePropRegion(def, placed.orientation, placed.state ?? undefined);
    const tex = texFor(frames, def.sprite, region) ?? Texture.EMPTY;

    const s = new Sprite(tex);
    s.anchor.set(0.5, 1);
    // footprint box bottom-center: tile is the top-left of a (footprint.w × footprint.h) box.
    const boxX = placed.tile.x * tileSize;
    const boxY = placed.tile.y * tileSize;
    s.x = boxX + (def.footprint.w * tileSize) / 2;
    s.y = boxY + def.footprint.h * tileSize; // sit on the bottom edge of the footprint
    if (mirror) s.scale.x = -1;
    s.eventMode = "none";

    this.sprite = s;
    this.groundY = s.y;
    this.footWidthPx = def.footprint.w * tileSize;

    // load the flicker clip (if any) into texture cycles honoring per-frame ms.
    if (def.anim && book[def.anim]) {
      for (const f of book[def.anim].frames) {
        const t = texFor(frames, f.sprite, f.region);
        if (t) {
          this.clip.push(t);
          this.frameMs.push(f.ms);
        }
      }
      if (this.clip.length > 0) this.sprite.texture = this.clip[0];
    }
  }

  /** True when this prop has a running animation clip (lantern/candle flicker). */
  get animated(): boolean {
    return this.clip.length > 1;
  }

  /** Advance the flicker clip by a clamped `dt` (no-op for static props / reduced motion). */
  update(dt: number, reduceMotion: boolean): void {
    if (reduceMotion || this.clip.length < 2) return;
    this.frameAcc += dt;
    const ms = this.frameMs[this.frameIdx] || 200;
    if (this.frameAcc >= ms) {
      this.frameAcc -= ms;
      this.frameIdx = (this.frameIdx + 1) % this.clip.length;
      this.sprite.texture = this.clip[this.frameIdx];
    }
  }

  destroy(): void {
    this.sprite.destroy();
  }
}
