// ghostopia web — the WS connection-state store.
//
// A tiny vanilla Zustand store the WS transports (`wsClient` live path, `SimClient` sim path)
// push their socket lifecycle into, so a `ConnectionIndicator` badge can show Connecting /
// Reconnecting / Disconnected without every consumer holding a socket reference. It imports NO
// SDK and NO key — it holds only a coarse lifecycle enum.

import { createStore } from "zustand/vanilla";

/** The coarse WS lifecycle the indicator renders. */
export type ConnectionState =
  | "idle" // no socket yet (not in a live/sim mode)
  | "connecting" // socket opening for the first time
  | "open" // connected
  | "reconnecting" // dropped unexpectedly, retrying
  | "disconnected"; // closed (intentionally or after giving up)

export interface ConnectionStoreState {
  state: ConnectionState;
  /** how many reconnect attempts since the last clean open (for the badge tooltip). */
  attempts: number;
  set: (state: ConnectionState) => void;
  /** note a reconnect attempt (bumps the counter + sets `reconnecting`). */
  noteReconnectAttempt: () => void;
  /** reset to idle (mode toggled off). */
  reset: () => void;
}

export const connectionStore = createStore<ConnectionStoreState>((set) => ({
  state: "idle",
  attempts: 0,
  set: (state) => set((s) => ({ state, attempts: state === "open" ? 0 : s.attempts })),
  noteReconnectAttempt: () => set((s) => ({ state: "reconnecting", attempts: s.attempts + 1 })),
  reset: () => set({ state: "idle", attempts: 0 }),
}));
