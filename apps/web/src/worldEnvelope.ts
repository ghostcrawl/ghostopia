// ghostopia web — apply the shared WORLD envelopes (critters + reactive props).
//
// Both the sim and live clients receive these server-authoritative envelopes; this is the
// ONE place that maps them onto the world store so the renderer draws critters + powers the
// reactive crypt-terminals. Imports NO backend package and NO key — it applies validated
// envelope data to the Zustand store only.

import { useWorldStore, type Critter } from "@ghostopia/ghost-renderer";

function critterFrom(o: Record<string, unknown>): (Partial<Critter> & { id: string }) | null {
  const id = typeof o.id === "string" ? o.id : null;
  if (!id) return null;
  const kind = o.kind === "wisp" || o.kind === "bat" ? o.kind : "cat";
  return {
    id,
    kind,
    x: typeof o.x === "number" ? o.x : 0,
    y: typeof o.y === "number" ? o.y : 0,
    state: o.state === "wander" || o.state === "follow" ? o.state : "idle",
    facing: typeof o.facing === "number" ? o.facing : 1,
    layer: o.layer === "overhead" ? "overhead" : kind === "cat" ? "ground" : "overhead",
  };
}

/**
 * Apply one WORLD envelope (critter.spawned / critter.update / critter.petted / prop.state) to
 * the store. Returns true when the type was a world envelope it handled (so a caller can skip
 * further routing), false otherwise.
 */
export function applyWorldEnvelope(type: string, payload: Record<string, unknown>): boolean {
  const store = useWorldStore.getState();
  switch (type) {
    case "critter.spawned": {
      const c = critterFrom(payload);
      if (c) store.upsertCritter(c);
      return true;
    }
    case "critter.update": {
      const list = Array.isArray(payload.critters) ? payload.critters : [];
      for (const raw of list) {
        if (typeof raw === "object" && raw !== null) {
          const c = critterFrom(raw as Record<string, unknown>);
          if (c) store.upsertCritter(c);
        }
      }
      return true;
    }
    case "critter.despawned": {
      if (typeof payload.id === "string") store.removeCritter(payload.id);
      return true;
    }
    case "critter.petted": {
      if (typeof payload.id === "string") store.petCritter(payload.id);
      return true;
    }
    case "prop.state": {
      const props = Array.isArray(payload.props) ? payload.props : [];
      const parsed: Array<{ id: string; active: boolean }> = [];
      for (const raw of props) {
        if (typeof raw === "object" && raw !== null) {
          const o = raw as Record<string, unknown>;
          if (typeof o.id === "string") parsed.push({ id: o.id, active: o.active === true });
        }
      }
      if (parsed.length > 0) store.setPropStates(parsed);
      return true;
    }
    default:
      return false;
  }
}
