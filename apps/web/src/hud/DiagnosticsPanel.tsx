// ghostopia web — the Diagnostics panel: REAL per-ghost + system health.
//
// A debug read-out driven ONLY by real state: per-ghost session state / last-event age /
// target / behavior (rosterStore + inspectorStore), and system pool occupancy + concurrency-
// governor headroom + WS connection (diagnosticsStore + connectionStore). An unknown value is
// shown as "unknown" / "—", NEVER invented (REAL-NOT-MOCK). Imports NO SDK / key.

import { useEffect, useState, type JSX } from "react";
import { useStore } from "zustand";

import { connectionStore } from "./connectionStore";
import { diagnosticsStore, lastEventAgeMs } from "./diagnosticsStore";
import { rosterStore } from "./rosterStore";
import { inspectorStore } from "../inspector/inspectorStore";

function ageLabel(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  return `${Math.round(ms / 60_000)}m`;
}

/**
 * The Diagnostics panel. Renders for the SELECTED ghost (its real session state / last-event
 * age / target / behavior) plus a system block (pool occupancy, concurrency-governor headroom,
 * WS state). Every field is real or shown as unknown.
 */
export function DiagnosticsPanel(): JSX.Element {
  const system = useStore(diagnosticsStore, (s) => s.system);
  const conn = useStore(connectionStore, (s) => s.state);
  const openId = useStore(inspectorStore, (s) => s.openGhostId);
  const sessions = useStore(inspectorStore, (s) => s.sessions);
  const ghosts = useStore(rosterStore, (s) => s.ghosts);

  // a light 1s tick so the "last-event age" read-out stays live without per-frame churn.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, []);

  const g = openId ? ghosts[openId] : undefined;
  const session = openId ? sessions[openId] : undefined;
  const age = session ? lastEventAgeMs(session.events, nowMs) : null;

  return (
    <section className="diagnostics" aria-label="diagnostics">
      <header className="diagnostics__head">diagnostics</header>

      <div className="diagnostics__group" aria-label="ghost diagnostics">
        <div className="diagnostics__grouptitle">ghost</div>
        {g ? (
          <dl className="diagnostics__list">
            <dt>id</dt>
            <dd>{g.ghostId}</dd>
            <dt>session state</dt>
            <dd>{g.state || "unknown"}</dd>
            <dt>behavior</dt>
            <dd>{g.behavior || "unknown"}</dd>
            <dt>section</dt>
            <dd>{g.section || "unknown"}</dd>
            <dt>target</dt>
            <dd title={g.task ?? undefined}>{g.task || "—"}</dd>
            <dt>last-event age</dt>
            <dd>{ageLabel(age)}</dd>
          </dl>
        ) : (
          <div className="diagnostics__empty">No ghost chosen yet…</div>
        )}
      </div>

      <div className="diagnostics__group" aria-label="system diagnostics">
        <div className="diagnostics__grouptitle">system</div>
        <dl className="diagnostics__list">
          <dt>ws</dt>
          <dd>{conn}</dd>
          <dt>pool</dt>
          <dd>{system ? `${system.poolActive} / ${system.poolMax} active` : "unknown"}</dd>
          <dt>governor headroom</dt>
          <dd>{system ? `${system.headroom} slot${system.headroom === 1 ? "" : "s"}` : "unknown"}</dd>
          <dt>queue depth</dt>
          <dd>{system ? `${system.queueDepth} waiting` : "unknown"}</dd>
          <dt>ghosts</dt>
          <dd>{system ? system.ghostCount : "unknown"}</dd>
        </dl>
        {system?.notice && (
          <div className="diagnostics__notice" role="status" aria-label="back-pressure">
            {system.notice}
          </div>
        )}
        {system && Object.keys(system.sections).length > 0 && (
          <ul className="diagnostics__sections">
            {Object.entries(system.sections).map(([sid, n]) => (
              <li key={sid}>
                <span className="diagnostics__sid">{sid}</span>
                <span className="diagnostics__count">{n}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
