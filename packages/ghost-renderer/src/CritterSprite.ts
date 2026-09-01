// ghostopia ghost-renderer — an autonomous graveyard critter sprite.
//
// Renders a server-authoritative `Critter` (black cat on the ground, will-o'-wisp / bat
// overhead). It plays the critter's clip on a ms clock, flips the ground cat by its travel
// facing, floats the overhead flyers with a gentle bob, and pops an ORIGINAL heart/spark
// overlay when the critter is petted (a click ack). Imports only PixiJS + the pure
// @ghostopia/ghost-art clip types — NO GhostCrawl SDK, NO Python package.

import { Container, Graphics, Sprite, type Texture } from "pixi.js";
import type { AnimationBook, Clip } from "@ghostopia/ghost-art";

import type { Critter } from "./contract.js";

/** critter kind → the clip it plays for a given FSM state. */
export function critterClipName(kind: Critter["kind"], state: Critter["state"]): string {
  if (kind === "cat") return state === "idle" ? "critter.cat.sit" : "critter.cat.walk";
  if (kind === "bat") return "world.bat";
  return "world.wisp";
}

/** Duration (ms) of the pet heart/spark flourish. */
export const PET_FLASH_MS = 800;

/** Per-frame UI flags for a critter. */
export interface CritterSpriteUi {
  /** prefers-reduced-motion: no overhead bob, and the pet flash is a quick static fade. */
  reduceMotion?: boolean;
}

/**
 * One rendered critter. Owns a Pixi `Container` at the critter's world-pixel coords holding
 * a bottom-anchored `Sprite` (cat) or centre-anchored `Sprite` (flyer) + a transient pet
 * overlay. `update(critter, dt, ui)` clips, plays, flips, and bobs it.
 */
export class CritterSprite {
  readonly container: Container;
  readonly kind: Critter["kind"];
  private readonly sprite: Sprite;
  private readonly frames: Record<string, Texture>;
  private readonly book: AnimationBook;

  private clipName: string;
  private frameIdx = 0;
  private frameAcc = 0;
  private bobT = 0;
  private spriteW = 0;
  private spriteH = 0;

  // ---- pet flash (heart for the cat, spark for the flyers) ----
  private pet: Graphics | null = null;
  private petAgeMs = 0;
  private petSeen = 0;

  constructor(kind: Critter["kind"], frames: Record<string, Texture>, book: AnimationBook) {
    this.kind = kind;
    this.frames = frames;
    this.book = book;
    this.clipName = critterClipName(kind, "idle");

    this.container = new Container();
    this.container.eventMode = "none";
    this.sprite = new Sprite();
    // the cat sits ON the ground (bottom-centre); flyers hang centred.
    this.sprite.anchor.set(0.5, kind === "cat" ? 1 : 0.5);
    this.container.addChild(this.sprite);
  }

  private clip(): Clip | undefined {
    return this.book[this.clipName];
  }

  private currentFrameTexture(): Texture | undefined {
    const clip = this.clip();
    if (!clip) return undefined;
    const f = clip.frames[Math.min(this.frameIdx, clip.frames.length - 1)];
    return this.frames[`${f.sprite}:${f.region}`];
  }

  private setFrame(tex: Texture | undefined): void {
    if (tex && this.sprite.texture !== tex) {
      this.sprite.texture = tex;
      this.spriteW = tex.width;
      this.spriteH = tex.height;
    }
  }

  /** Advance + render this critter for a clamped frame delta (ms). */
  update(
    critter: Critter,
    dtMs: number,
    ui: CritterSpriteUi = {},
    petAt: number | undefined = undefined,
  ): void {
    this.container.position.set(critter.x, critter.y);

    // pick the clip for the kind + state; reset the cursor on a change.
    const next = critterClipName(critter.kind, critter.state);
    if (next !== this.clipName) {
      this.clipName = next;
      this.frameIdx = 0;
      this.frameAcc = 0;
    }
    const clip = this.clip();
    if (clip) {
      this.frameAcc += dtMs;
      let safety = 0;
      while (this.frameAcc >= clip.frames[this.frameIdx].ms && safety < 64) {
        this.frameAcc -= clip.frames[this.frameIdx].ms;
        this.frameIdx = (this.frameIdx + 1) % clip.frames.length;
        safety += 1;
      }
      this.setFrame(this.currentFrameTexture());
    }

    // the ground cat faces its travel direction (art authored facing RIGHT).
    if (critter.kind === "cat") {
      this.sprite.scale.x = critter.facing < 0 ? -1 : 1;
      this.sprite.y = 0;
    } else {
      // overhead flyers get a gentle vertical bob (disabled under reduced motion).
      this.bobT = (this.bobT + dtMs) % 2000;
      const lift = ui.reduceMotion ? 0 : Math.sin((this.bobT / 2000) * Math.PI * 2) * 2;
      this.sprite.y = lift;
    }

    this.updatePet(petAt, dtMs, ui);
  }

  /** Draw/refresh the pet heart/spark flourish when a fresh pet timestamp arrives. */
  private updatePet(petAt: number | undefined, dtMs: number, ui: CritterSpriteUi): void {
    if (petAt !== undefined && petAt !== this.petSeen) {
      this.petSeen = petAt;
      this.petAgeMs = 0;
      if (!this.pet) {
        this.pet = this.kind === "cat" ? makeHeart() : makeSpark();
        this.container.addChild(this.pet);
      }
      this.pet.visible = true;
      this.pet.alpha = 1;
      this.pet.scale.set(1);
    }
    if (this.pet && this.pet.visible) {
      this.petAgeMs += dtMs;
      const t = this.petAgeMs / PET_FLASH_MS;
      if (t >= 1) {
        this.pet.visible = false;
      } else {
        const headY = this.kind === "cat" ? -this.spriteH - 4 : -this.spriteH * 0.5 - 4;
        const rise = ui.reduceMotion ? 6 : 6 + t * 10;
        this.pet.position.set(0, headY - rise);
        this.pet.alpha = t < 0.5 ? 1 : Math.max(0, 1 - (t - 0.5) / 0.5);
        if (!ui.reduceMotion) this.pet.scale.set(0.8 + t * 0.5);
      }
    }
  }

  /** The critter's world-space footprint for pointer hit-testing, or null before sizing. */
  hitBox(): { x: number; y: number; w: number; h: number } | null {
    const w = this.spriteW;
    const h = this.spriteH;
    if (w < 2 || h < 2) return null;
    const cx = this.container.x;
    const cy = this.container.y;
    // cat: bottom-centre anchored; flyer: centre anchored. Pad the hit-box a touch for tapping.
    const pad = 3;
    const top = this.kind === "cat" ? cy - h : cy - h / 2;
    return { x: cx - w / 2 - pad, y: top - pad, w: w + pad * 2, h: h + pad * 2 };
  }

  destroy(): void {
    this.container.destroy({ children: true });
  }
}

/** An ORIGINAL small pink pixel heart (drawn, not traced). */
function makeHeart(): Graphics {
  const g = new Graphics();
  const c = 0xff5aa8;
  // two lobes + a point — a compact 7px heart.
  g.circle(-2, -1, 2.2).fill(c);
  g.circle(2, -1, 2.2).fill(c);
  g.poly([-4, 0, 4, 0, 0, 5]).fill(c);
  g.eventMode = "none";
  return g;
}

/** An ORIGINAL green spectral spark (four-point twinkle) for the flyers. */
function makeSpark(): Graphics {
  const g = new Graphics();
  const c = 0x8be04a;
  g.poly([0, -5, 1.4, -1.4, 5, 0, 1.4, 1.4, 0, 5, -1.4, 1.4, -5, 0, -1.4, -1.4]).fill(c);
  g.circle(0, 0, 1.4).fill(0xeafff0);
  g.blendMode = "add";
  g.eventMode = "none";
  return g;
}
