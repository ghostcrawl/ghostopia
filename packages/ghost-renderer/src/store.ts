// ghostopia ghost-renderer — the server-authoritative-state <-> render bridge.
//
// A vanilla Zustand store (readable OUTSIDE React via `getState()`, so the
// PixiJS ticker can read it every frame with zero React re-renders). React chrome
// (the HUD) subscribes to the SAME store via `useStore(useWorldStore, sel)`.
//
// The ghost shape MIRRORS the Pydantic contract (contract.ts). In later
// waves the authed WS feeds server envelopes into this same store; the
// renderer never knows whether a ghost was seeded client-side (Stage 1) or
// pushed by the server. Imports NO GhostCrawl SDK / Python package.

import { createStore } from "zustand/vanilla";

import {
  clampCamera,
  panByScreen,
  zoomByFactor,
  zoomAtPoint,
  followStep,
  type Camera,
  type CameraBounds,
} from "./Camera.js";
import type { Bubble, Critter, Ghost, GhostStatusChanged, Point } from "./contract.js";

/** Default bubble lifetime (ms) before it fully fades. */
export const DEFAULT_BUBBLE_TTL_MS = 2600;

/** The render-bridge state + its setters. */
export interface WorldState {
  /** id -> Ghost (a map so upserts are O(1) and stable across frames). */
  ghosts: Record<string, Ghost>;
  /** ghostId -> the ONE active speech/thought bubble above that ghost (fades in the ticker). */
  bubbles: Record<string, Bubble>;
  /** ghostId -> the wall-clock ms a 🔊 sound-cue fired over that ghost (fades in the ticker). */
  soundPings: Record<string, number>;
  /** id -> autonomous graveyard Critter (server-authoritative FSM). */
  critters: Record<string, Critter>;
  /** critterId -> the wall-clock ms it was petted (a heart/spark flash the ticker fades). */
  critterPets: Record<string, number>;
  /** prop (workstation) id -> whether it is currently ACTIVE (a working ghost is at it). */
  activeProps: Record<string, boolean>;
  camera: Camera;
  /** optional world-space clamp bounds (set once the map size is known). */
  cameraBounds: CameraBounds | null;
  selectedGhostId: string | null;
  /** "always show labels" toggle — draw every ghost's name tag, not just the selected one. */
  showLabels: boolean;
  /** auto-follow toggle — when true the camera lerps to the selected ghost. */
  followEnabled: boolean;

  /** Insert or merge a ghost by id (the server envelope / seed shape). */
  upsertGhost: (ghost: Partial<Ghost> & { id: string }) => void;
  /** Remove a ghost by id (e.g. a ghost.despawned envelope). */
  removeGhost: (id: string) => void;
  /** Push a transient bubble above a ghost (server say/overlay). Replaces any prior one. */
  pushBubble: (ghostId: string, text: string, kind?: "say" | "overlay", ttlMs?: number) => void;
  /** Remove a ghost's bubble (the ticker calls this when it has fully faded). */
  clearBubble: (ghostId: string) => void;
  /** Flash a 🔊 sound-cue indicator over a ghost; the ticker fades + clears it. */
  pingSound: (ghostId: string) => void;
  /** Remove a ghost's 🔊 ping (the ticker calls this once it has fully faded). */
  clearSoundPing: (ghostId: string) => void;
  /** Insert or merge a critter by id (server `critter.spawned` / `critter.update`). */
  upsertCritter: (critter: Partial<Critter> & { id: string }) => void;
  /** Remove a critter by id (server `critter.despawned`). */
  removeCritter: (id: string) => void;
  /** Flash a pet heart/spark over a critter (a `critter.petted` ack); the ticker fades it. */
  petCritter: (id: string) => void;
  /** Remove a critter's pet flash (the ticker calls this once it has fully faded). */
  clearCritterPet: (id: string) => void;
  /** Apply a server `prop.state` batch — set each prop's active flag. */
  setPropStates: (props: Array<{ id: string; active: boolean }>) => void;
  /** Apply a `ghost.status_changed`-shaped update (merges by ghost_id). */
  applyStatusChanged: (update: GhostStatusChanged) => void;
  /** Move a ghost to a world/tile position (server-authoritative placement). */
  setGhostPosition: (id: string, position: Point) => void;
  /**
   * BATCHED per-frame placement (Pitfall 2): apply ALL moved ghosts'
   * positions in ONE store `set` (a single fresh `ghosts` object) instead of N calls.
   * The rAF walk-interpolation loop accumulates every moved ghost for the frame and
   * flushes here once — bounding GC churn + HUD re-render pressure at 25–50 ghosts.
   * Unknown ids are skipped (no phantom placement).
   */
  setGhostPositions: (positions: Record<string, Point>) => void;

  /** Replace the camera (clamped to zoom + optional bounds). */
  setCamera: (camera: Partial<Camera>) => void;
  /** Pan by a SCREEN-space delta (pointer drag). */
  panCamera: (screenDx: number, screenDy: number) => void;
  /** Multiply zoom by a factor (wheel step / +- buttons — centre-anchored). */
  zoomCamera: (factor: number) => void;
  /** Zoom by a factor keeping a WORLD point fixed (wheel-at-cursor / pinch midpoint). */
  zoomCameraAt: (factor: number, worldX: number, worldY: number) => void;
  /** Smooth-follow the camera a fraction `alpha` toward a world point (selected-ghost follow). */
  followTo: (worldX: number, worldY: number, alpha: number) => void;
  /** Set the world-space camera clamp bounds (re-clamps the current camera). */
  setCameraBounds: (bounds: CameraBounds | null) => void;

  /** Select (or clear) the focused ghost. */
  selectGhost: (id: string | null) => void;
  /** Toggle / set the "always show labels" overlay flag. */
  setShowLabels: (on: boolean) => void;
  /** Toggle / set the auto-follow-camera flag. */
  setFollowEnabled: (on: boolean) => void;
}

const DEFAULT_CAMERA: Camera = { x: 0, y: 0, zoom: 2 };

/**
 * The vanilla world store. `useWorldStore.getState()` reads it from the ticker;
 * `useStore(useWorldStore, selector)` (from `zustand`) subscribes React chrome.
 */
export const useWorldStore = createStore<WorldState>((set, get) => ({
  ghosts: {},
  bubbles: {},
  soundPings: {},
  critters: {},
  critterPets: {},
  activeProps: {},
  camera: { ...DEFAULT_CAMERA },
  cameraBounds: null,
  selectedGhostId: null,
  showLabels: false,
  followEnabled: true,

  upsertGhost: (ghost) =>
    set((s) => {
      const prev = s.ghosts[ghost.id];
      const merged: Ghost = prev
        ? { ...prev, ...ghost }
        : {
            id: ghost.id,
            name: ghost.name ?? ghost.id,
            home_grave: ghost.home_grave ?? "",
            position: ghost.position ?? null,
            section: ghost.section ?? null,
            state: ghost.state ?? "IDLE",
            task_id: ghost.task_id ?? null,
            behavior_override: ghost.behavior_override ?? null,
            color: ghost.color ?? null,
          };
      return { ghosts: { ...s.ghosts, [ghost.id]: merged } };
    }),

  removeGhost: (id) =>
    set((s) => {
      if (!s.ghosts[id]) return {};
      const next = { ...s.ghosts };
      delete next[id];
      const bubbles = s.bubbles[id] ? { ...s.bubbles } : s.bubbles;
      if (s.bubbles[id]) delete bubbles[id];
      const soundPings = s.soundPings[id] !== undefined ? { ...s.soundPings } : s.soundPings;
      if (s.soundPings[id] !== undefined) delete soundPings[id];
      return {
        ghosts: next,
        bubbles,
        soundPings,
        selectedGhostId: s.selectedGhostId === id ? null : s.selectedGhostId,
      };
    }),

  pushBubble: (ghostId, text, kind = "say", ttlMs = DEFAULT_BUBBLE_TTL_MS) =>
    set((s) => {
      const trimmed = text.trim();
      if (!trimmed) return {};
      const bubble: Bubble = {
        ghostId,
        text: trimmed.length > 80 ? `${trimmed.slice(0, 79)}…` : trimmed,
        kind,
        createdMs: Date.now(),
        ttlMs,
      };
      return { bubbles: { ...s.bubbles, [ghostId]: bubble } };
    }),

  clearBubble: (ghostId) =>
    set((s) => {
      if (!s.bubbles[ghostId]) return {};
      const bubbles = { ...s.bubbles };
      delete bubbles[ghostId];
      return { bubbles };
    }),

  pingSound: (ghostId) =>
    set((s) => {
      if (!s.ghosts[ghostId]) return {}; // no phantom pings for unknown ghosts
      return { soundPings: { ...s.soundPings, [ghostId]: Date.now() } };
    }),

  clearSoundPing: (ghostId) =>
    set((s) => {
      if (s.soundPings[ghostId] === undefined) return {};
      const soundPings = { ...s.soundPings };
      delete soundPings[ghostId];
      return { soundPings };
    }),

  upsertCritter: (critter) =>
    set((s) => {
      const prev = s.critters[critter.id];
      const merged: Critter = prev
        ? { ...prev, ...critter }
        : {
            id: critter.id,
            kind: critter.kind ?? "cat",
            x: critter.x ?? 0,
            y: critter.y ?? 0,
            state: critter.state ?? "idle",
            facing: critter.facing ?? 1,
            layer: critter.layer ?? (critter.kind === "cat" ? "ground" : "overhead"),
          };
      return { critters: { ...s.critters, [critter.id]: merged } };
    }),

  removeCritter: (id) =>
    set((s) => {
      if (!s.critters[id]) return {};
      const next = { ...s.critters };
      delete next[id];
      const pets = s.critterPets[id] !== undefined ? { ...s.critterPets } : s.critterPets;
      if (s.critterPets[id] !== undefined) delete pets[id];
      return { critters: next, critterPets: pets };
    }),

  petCritter: (id) =>
    set((s) => {
      if (!s.critters[id]) return {}; // no phantom pet for an unknown critter
      return { critterPets: { ...s.critterPets, [id]: Date.now() } };
    }),

  clearCritterPet: (id) =>
    set((s) => {
      if (s.critterPets[id] === undefined) return {};
      const pets = { ...s.critterPets };
      delete pets[id];
      return { critterPets: pets };
    }),

  setPropStates: (props) =>
    set((s) => {
      let changed = false;
      const next = { ...s.activeProps };
      for (const p of props) {
        if (next[p.id] !== p.active) {
          next[p.id] = p.active;
          changed = true;
        }
      }
      return changed ? { activeProps: next } : {};
    }),

  applyStatusChanged: (update) =>
    set((s) => {
      const prev = s.ghosts[update.ghost_id];
      if (!prev) return {}; // status for an unknown ghost is ignored (no phantom)
      const next: Ghost = { ...prev };
      if (update.state !== undefined) next.state = update.state;
      if (update.section !== undefined) next.section = update.section;
      if (update.position !== undefined) next.position = update.position;
      if (update.task_id !== undefined) next.task_id = update.task_id;
      // thread the explicit server facing so a stationary working
      // ghost orients to its workstation (honored in GhostSprite while !moving).
      if (update.facing !== undefined) next.facing = update.facing;
      return { ghosts: { ...s.ghosts, [update.ghost_id]: next } };
    }),

  setGhostPosition: (id, position) =>
    set((s) => {
      const prev = s.ghosts[id];
      if (!prev) return {};
      return { ghosts: { ...s.ghosts, [id]: { ...prev, position } } };
    }),

  setGhostPositions: (positions) =>
    set((s) => {
      let changed = false;
      const next = { ...s.ghosts };
      for (const id in positions) {
        const prev = next[id];
        if (!prev) continue; // no phantom placement for an unknown ghost
        next[id] = { ...prev, position: positions[id] };
        changed = true;
      }
      return changed ? { ghosts: next } : {};
    }),

  setCamera: (camera) =>
    set((s) => ({
      camera: clampCamera({ ...s.camera, ...camera }, s.cameraBounds ?? undefined),
    })),

  panCamera: (screenDx, screenDy) =>
    set((s) => ({
      camera: panByScreen(s.camera, screenDx, screenDy, s.cameraBounds ?? undefined),
    })),

  zoomCamera: (factor) =>
    set((s) => ({
      camera: zoomByFactor(s.camera, factor, s.cameraBounds ?? undefined),
    })),

  zoomCameraAt: (factor, worldX, worldY) =>
    set((s) => ({
      camera: zoomAtPoint(s.camera, factor, worldX, worldY, s.cameraBounds ?? undefined),
    })),

  followTo: (worldX, worldY, alpha) =>
    set((s) => ({
      camera: followStep(s.camera, worldX, worldY, alpha, s.cameraBounds ?? undefined),
    })),

  setCameraBounds: (bounds) =>
    set((s) => ({
      cameraBounds: bounds,
      camera: clampCamera(s.camera, bounds ?? undefined),
    })),

  selectGhost: (id) => {
    // ignore a select for a ghost that does not exist (keeps HUD honest)
    if (id !== null && !get().ghosts[id]) return;
    set({ selectedGhostId: id });
  },

  setShowLabels: (on) => set({ showLabels: on }),
  setFollowEnabled: (on) => set({ followEnabled: on }),
}));

/** Convenience typed accessor for the ticker (identical to `.getState()`). */
export function getWorldState(): WorldState {
  return useWorldStore.getState();
}
