// ghostopia web — the roster store (STAGE 5).
//
// A vanilla Zustand store holding each ghost's LIGHTWEIGHT status (state / active behavior /
// current task / progress / record count / section) — fed by the server's frame-FREE
// `ghost.status_changed` cadence (`status_poll.py`). This is the roster HUD's data
// source; only the SELECTED ghost gets the expensive frame stream (that lives in
// `inspectorStore`). This file imports NO GhostCrawl SDK and NO key.

import { createStore } from "zustand/vanilla";

import type { GhostAttention } from "@ghostopia/ghost-renderer";

/** One ghost's lightweight roster status (never a frame — status only). */
export interface RosterEntry {
  ghostId: string;
  name: string;
  section: string;
  behavior: string;
  state: string;
  task: string | null;
  currentUrl: string | null;
  progress: number;
  records: number;
  /** operator-attention flag from the status envelope; null → no alert. */
  attention?: GhostAttention | null;
}

export interface RosterState {
  /** ghost_id -> its latest lightweight status. */
  ghosts: Record<string, RosterEntry>;
  /** Apply a server `ghost.status_changed` snapshot (upsert). */
  applyStatus: (entry: RosterEntry) => void;
  /** Seed a row from a `ghost.spawned` (name/section/behavior known up front). */
  seed: (ghostId: string, name: string, section: string, behavior: string) => void;
  /** Drop a ghost (despawn / Live toggle-off clear). */
  remove: (ghostId: string) => void;
  /** Clear the whole roster (Live toggle-off). */
  clear: () => void;
}

export const rosterStore = createStore<RosterState>((set) => ({
  ghosts: {},

  applyStatus: (entry) =>
    set((s) => ({ ghosts: { ...s.ghosts, [entry.ghostId]: { ...s.ghosts[entry.ghostId], ...entry } } })),

  seed: (ghostId, name, section, behavior) =>
    set((s) => {
      if (s.ghosts[ghostId]) return s;
      return {
        ghosts: {
          ...s.ghosts,
          [ghostId]: {
            ghostId,
            name,
            section,
            behavior,
            state: "IDLE",
            task: null,
            currentUrl: null,
            progress: 0,
            records: 0,
          },
        },
      };
    }),

  remove: (ghostId) =>
    set((s) => {
      const next = { ...s.ghosts };
      delete next[ghostId];
      return { ghosts: next };
    }),

  clear: () => set({ ghosts: {} }),
}));

/** Group the roster rows by section id (section header + rows), stable-sorted by name. */
export function groupBySection(ghosts: Record<string, RosterEntry>): Array<{
  section: string;
  rows: RosterEntry[];
}> {
  const bySection = new Map<string, RosterEntry[]>();
  for (const g of Object.values(ghosts)) {
    const arr = bySection.get(g.section) ?? [];
    arr.push(g);
    bySection.set(g.section, arr);
  }
  return [...bySection.entries()]
    .map(([section, rows]) => ({
      section,
      rows: [...rows].sort((a, b) => a.name.localeCompare(b.name)),
    }))
    .sort((a, b) => a.section.localeCompare(b.section));
}
