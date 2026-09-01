// ghostopia web — the global dashboard (STAGE 7), behavior/section-aware.
//
// The command-center metrics: ghosts active/idle, browser sessions, pages crawled, pages/min,
// records, completed/failed tasks, retries, captcha events, rate limits, avg page time, and
// current missions — PLUS per-section throughput and per-behavior activity. Every value is
// sourced from REAL server state: the roster store (per-ghost state/behavior, from the
// frame-free status poll), the results store (DB-computed records/throughput/missions), and
// the metrics store (real-stream nav/session/error counters). No metric is invented — an
// unbacked counter reads 0. This file imports NO GhostCrawl SDK and NO key.

import type { JSX } from "react";
import { useStore } from "zustand";

import {
  aggregateThroughput,
  resultsStore,
} from "./resultsStore";
import { avgPageSeconds, metricsStore, pagesPerMinute } from "./metricsStore";
import { rosterStore, type RosterEntry } from "./rosterStore";

function Stat({ label, value }: { label: string; value: string | number }): JSX.Element {
  return (
    <div className="dash__stat">
      <span className="dash__stat-value">{value}</span>
      <span className="dash__stat-label">{label}</span>
    </div>
  );
}

/** Count ghosts by active (working) vs idle from the live roster. */
function ghostCounts(ghosts: Record<string, RosterEntry>): { active: number; idle: number } {
  let active = 0;
  let idle = 0;
  for (const g of Object.values(ghosts)) {
    if (g.state === "IDLE" || g.state === "RETURNING_HOME") idle += 1;
    else active += 1;
  }
  return { active, idle };
}

/** Per-behavior activity: how many ghosts are currently running each behavior. */
function behaviorTally(ghosts: Record<string, RosterEntry>): Array<{ behavior: string; count: number }> {
  const tally = new Map<string, number>();
  for (const g of Object.values(ghosts)) {
    if (!g.behavior) continue;
    tally.set(g.behavior, (tally.get(g.behavior) ?? 0) + 1);
  }
  return [...tally.entries()]
    .map(([behavior, count]) => ({ behavior, count }))
    .sort((a, b) => b.count - a.count);
}

/** The global command-center dashboard — real metrics + per-section/behavior. */
export function Dashboard(): JSX.Element {
  const ghosts = useStore(rosterStore, (s) => s.ghosts);
  const sections = useStore(resultsStore, (s) => s.sections);
  const missions = useStore(resultsStore, (s) => s.missions);
  const metrics = useStore(metricsStore, (s) => s);

  const { active, idle } = ghostCounts(ghosts);
  const agg = aggregateThroughput(sections);
  const behaviors = behaviorTally(ghosts);
  const liveMissions = Object.values(missions);
  const currentMissions = liveMissions.filter(
    (m) => !m.progress || m.progress.completed + m.progress.failed < m.progress.total,
  );

  return (
    <section className="dash" aria-label="global dashboard">
      <header className="dash__head">command center</header>
      <div className="dash__grid">
        <Stat label="ghosts active" value={active} />
        <Stat label="ghosts idle" value={idle} />
        <Stat label="sessions" value={metrics.sessionsOpened} />
        <Stat label="pages crawled" value={metrics.pagesCrawled} />
        <Stat label="pages/min" value={pagesPerMinute(metrics.navTimes).toFixed(1)} />
        <Stat label="records" value={agg.records} />
        <Stat label="completed" value={agg.completed} />
        <Stat label="failed" value={agg.failed} />
        <Stat label="retries" value={metrics.retries} />
        <Stat label="captcha" value={metrics.captchaEvents} />
        <Stat label="rate limits" value={metrics.rateLimits} />
        <Stat label="avg page s" value={avgPageSeconds(metrics.navTimes).toFixed(1)} />
      </div>

      <div className="dash__section">
        <div className="dash__section-head">missions ({currentMissions.length} active)</div>
        <ul className="dash__missions">
          {currentMissions.length === 0 && <li className="dash__empty">No missions afoot yet…</li>}
          {currentMissions.map((m) => (
            <li className="dash__mission" key={m.id}>
              <span className="dash__mission-title" title={m.id}>{m.title}</span>
              <span className="dash__mission-prog">
                {m.progress ? `${m.progress.completed + m.progress.failed}/${m.progress.total}` : `0/${m.total}`}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="dash__section">
        <div className="dash__section-head">per section</div>
        <ul className="dash__sections">
          {sections.length === 0 && <li className="dash__empty">The halls are still…</li>}
          {sections.map((s) => (
            <li className="dash__srow" key={s.section || "—"}>
              <span className="dash__sname">{s.section || "—"}</span>
              <span className="dash__sstat">{s.records} rec</span>
              <span className="dash__sstat">{s.completed}✓</span>
              {s.failed > 0 && <span className="dash__sfail">{s.failed}✗</span>}
            </li>
          ))}
        </ul>
      </div>

      <div className="dash__section">
        <div className="dash__section-head">per behavior</div>
        <ul className="dash__behaviors">
          {behaviors.length === 0 && <li className="dash__empty">The graveyard is quiet…</li>}
          {behaviors.map((b) => (
            <li className="dash__brow" key={b.behavior}>
              <span className="dash__bname">{b.behavior}</span>
              <span className="dash__bcount">{b.count}</span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
