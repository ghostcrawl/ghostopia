// ghostopia ghost-renderer — per-kind work clip map tests .
//
// Asserts that EVERY work state resolves to a clip that actually exists in the shipped
// animations.json — so a per-kind work pose can never fall back to a blank/idle frame.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

import { describe, expect, it } from "vitest";

import {
  actionKindForState,
  clipNameForGhost,
  WORK_STATE_CLIP,
} from "./GhostSprite.js";
import { ACTION_GLYPH_KINDS } from "./overlays.js";
import type { GhostState } from "./contract.js";

const here = dirname(fileURLToPath(import.meta.url));
const animations = JSON.parse(
  readFileSync(resolve(here, "../../../assets/animations.json"), "utf8"),
) as { clips: Record<string, unknown> };
const clips = animations.clips;

const WORK_STATES: GhostState[] = [
  "NAVIGATING",
  "SEARCHING",
  "READING",
  "SCROLLING",
  "EXTRACTING",
];

describe("per-kind work clips", () => {
  it("every specific work state maps to an EXISTING work.<kind> clip", () => {
    for (const state of WORK_STATES) {
      const clip = clipNameForGhost(state, "s", false);
      expect(clip).toBe(WORK_STATE_CLIP[state]);
      expect(clip.startsWith("work.")).toBe(true);
      expect(clips[clip], `missing clip ${clip}`).toBeDefined();
    }
  });

  it("generic work states fall back to the plain 'work' clip", () => {
    for (const state of ["OPENING_BROWSER", "AT_WORKSTATION", "PROCESSING", "WAITING", "RETRYING"] as GhostState[]) {
      expect(clipNameForGhost(state, "s", false)).toBe("work");
      expect(clips["work"]).toBeDefined();
    }
  });

  it("every action glyph kind matches a work state that resolves to it", () => {
    for (const state of WORK_STATES) {
      const kind = actionKindForState(state);
      expect(kind).not.toBeNull();
      expect(ACTION_GLYPH_KINDS).toContain(kind);
    }
    // idle/non-work states have no action glyph
    expect(actionKindForState("IDLE")).toBeNull();
    expect(actionKindForState("WALKING")).toBeNull();
  });
});
