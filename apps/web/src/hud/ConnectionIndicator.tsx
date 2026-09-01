// ghostopia web — the WS connection-state badge.
//
// A small always-available badge bound to the shared `connectionStore` (fed by the live/sim WS
// transports). It surfaces Connecting / Reconnecting / Disconnected so the operator knows when
// the world is (or isn't) live — and stays quiet (no badge) when the socket is healthily open
// or no mode is active. Imports NO SDK and NO key — it renders a coarse lifecycle enum only.

import type { JSX } from "react";
import { useStore } from "zustand";

import { connectionStore, type ConnectionState } from "./connectionStore";

/** The visible label + modifier class per lifecycle state (open/idle render nothing). */
const LABELS: Partial<Record<ConnectionState, { text: string; mod: string }>> = {
  connecting: { text: "Connecting…", mod: "connecting" },
  reconnecting: { text: "Reconnecting…", mod: "reconnecting" },
  disconnected: { text: "Disconnected", mod: "disconnected" },
};

/**
 * The connection badge. Shows nothing while `open` (healthy) or `idle` (no mode) — it only
 * appears to flag a NON-live socket, so it never clutters the world in the happy path.
 */
export function ConnectionIndicator(): JSX.Element {
  const state = useStore(connectionStore, (s) => s.state);
  const attempts = useStore(connectionStore, (s) => s.attempts);
  const info = LABELS[state];
  if (!info) return <></>;
  return (
    <div
      className={`conn conn--${info.mod}`}
      role="status"
      aria-live="polite"
      title={attempts > 0 ? `${info.text} (attempt ${attempts})` : info.text}
    >
      <span className="conn__dot" />
      <span className="conn__label">{info.text}</span>
    </div>
  );
}
