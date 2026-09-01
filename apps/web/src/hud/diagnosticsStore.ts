// ghostopia web — the system-diagnostics store.
//
// A tiny vanilla Zustand store fed by the server's frame-free `diagnostics.system` envelope
// (REAL pool state: concurrency-governor headroom + per-section occupancy). The
// DiagnosticsPanel subscribes to it. Holds only real values — an unknown metric is absent, so
// the panel renders it as "unknown" rather than inventing a number. Imports NO SDK / key.

import { createStore } from "zustand/vanilla";

/** A frame-free SYSTEM diagnostics snapshot (all fields REAL, from the pool). */
export interface SystemDiagnostics {
  /** ghosts whose run is in flight (semaphore-held). */
  poolActive: number;
  /** the pool's hard concurrency cap (the real governor limit). */
  poolMax: number;
  /** remaining concurrency headroom (poolMax − poolActive). */
  headroom: number;
  /** tasks waiting for a free slot across sections — the DEFER/back-pressure depth. */
  queueDepth: number;
  /** true when the pool is at its cap (active ≥ poolMax) — work is queuing, not erroring. */
  saturated: boolean;
  /** curated, surface-safe back-pressure notice ("Waiting for a free lantern…") or null. */
  notice: string | null;
  /** total ghosts the pool tracks. */
  ghostCount: number;
  /** section id → rostered ghost count. */
  sections: Record<string, number>;
  /** wall-clock ms the snapshot arrived (for a freshness read-out). */
  updatedMs: number;
}

export interface DiagnosticsState {
  system: SystemDiagnostics | null;
  applySystem: (raw: Record<string, unknown>) => void;
  clear: () => void;
}

function numOr(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

export const diagnosticsStore = createStore<DiagnosticsState>((set) => ({
  system: null,
  applySystem: (raw) =>
    set(() => {
      const sections: Record<string, number> = {};
      if (typeof raw.sections === "object" && raw.sections !== null) {
        for (const [k, v] of Object.entries(raw.sections as Record<string, unknown>)) {
          if (typeof v === "number") sections[k] = v;
        }
      }
      return {
        system: {
          poolActive: numOr(raw.pool_active, 0),
          poolMax: numOr(raw.pool_max, 0),
          headroom: numOr(raw.headroom, 0),
          queueDepth: numOr(raw.queue_depth, 0),
          saturated: raw.saturated === true,
          // curated server copy only (already surface-safe) — never invented client-side.
          notice: typeof raw.notice === "string" && raw.notice.trim() !== "" ? raw.notice : null,
          ghostCount: numOr(raw.ghost_count, 0),
          sections,
          updatedMs: Date.now(),
        },
      };
    }),
  clear: () => set({ system: null }),
}));

/**
 * PURE: the age (ms) of the most-recent activity event for a ghost, or null when it has none.
 * Used by the DiagnosticsPanel's "last-event age" read-out — a real freshness signal, never a
 * fabricated one (a ghost with no events shows "—", not a made-up age).
 */
export function lastEventAgeMs(
  events: ReadonlyArray<{ ts: number }>,
  nowMs: number,
): number | null {
  if (events.length === 0) return null;
  let newest = events[0].ts;
  for (const e of events) if (e.ts > newest) newest = e.ts;
  // event ts are in SECONDS (envelope ts); convert to ms for the age.
  return Math.max(0, nowMs - newest * 1000);
}
