// ghostopia web — the section-grouped roster HUD (STAGE 5).
//
// A live list of ALL working ghosts GROUPED BY SECTION (section header + roster count), each
// row showing the ghost's name, state, active behavior name, current task, a progress bar, and
// record count. Rows are fed by the frame-FREE `ghost.status_changed` cadence (`rosterStore`);
// clicking a row selects that ghost (`ghost.select`) so the server streams ITS live frames
// (only one at a time). Selection ALSO works by clicking the ghost sprite on the
// PixiJS canvas (render-loop pointer hit-test → the same `ghost.select`); this HTML roster is
// an equivalent path, not the only one.
//
// This file imports NO GhostCrawl SDK and NO key — it renders only what the server relayed.

import type { JSX } from "react";
import { useStore } from "zustand";

import { useWorldStore } from "@ghostopia/ghost-renderer";

import type { ManageCommand } from "../liveClient";
import { openInspectorFor } from "../inspector/InspectorPanel";
import { inspectorStore } from "../inspector/inspectorStore";
import { safeSurfaceText } from "../surfaceSafe";
import { catalogStore } from "./catalogStore";
import { ghostStateLabel } from "./ghostStateLabel";
import { groupBySection, rosterStore, type RosterEntry } from "./rosterStore";

function pct(progress: number): string {
  return `${Math.round(Math.max(0, Math.min(1, progress)) * 100)}%`;
}

/** Select a ghost + fit the camera on it (double-click zoom-to-ghost). */
function zoomToGhost(ghostId: string, onSelect: (id: string | null) => void): void {
  onSelect(ghostId);
  const g = useWorldStore.getState().ghosts[ghostId];
  const pos = g?.position;
  if (pos) useWorldStore.getState().setCamera({ x: pos.x, y: pos.y, zoom: 4 });
}

/**
 * The section-grouped roster overlay. `onSelect(ghostId)` drives the live client's
 * `ghost.select` (frame fan-out). `onManage(cmd)` drives the runtime management surface
 * (pause/resume/retarget) — NAMES only, applied server-authoritatively. Hovering a row shows
 * the status popup; clicking opens the inspector for that ghost.
 */
export function GhostRoster({
  onSelect,
  onManage,
}: {
  onSelect: (ghostId: string | null) => void;
  onManage?: (cmd: ManageCommand) => void;
}): JSX.Element {
  const ghosts = useStore(rosterStore, (s) => s.ghosts);
  const openId = useStore(inspectorStore, (s) => s.openGhostId);
  // 196 FIX 4: the retarget button targets a REAL current department, read from the server's
  // relayed catalog (`kind === "department"`) — the same authoritative section source the world
  // uses — NOT the removed "verify"/"research"/"extraction" stage sections (dropped in 194/195).
  // If the catalog carries no department yet, the retarget control is omitted (never a dead id).
  const sections = useStore(catalogStore, (s) => s.sections);
  const departments = sections.filter((s) => s.kind === "department");
  const groups = groupBySection(ghosts);
  if (groups.length === 0) return <></>;

  return (
    <div className="roster" aria-label="working ghosts">
      {groups.map(({ section, rows }) => {
        // Prefer a department OTHER than this group's own section (a real move), else the first
        // department. `undefined` when the catalog has no department → the button is omitted.
        const retargetTo =
          departments.find((d) => d.id !== section) ?? departments[0] ?? undefined;
        return (
        <section className="roster__group" key={section}>
          <header className="roster__section-head">
            <span className="roster__section-name">{section}</span>
            <span className="roster__section-count">{rows.length}</span>
          </header>
          <ul className="roster__list">
            {rows.map((g: RosterEntry) => (
              <li key={g.ghostId}>
                <button
                  type="button"
                  className={`roster__row${g.ghostId === openId ? " roster__row--open" : ""}${
                    g.attention?.needs ? " roster__row--attention" : ""
                  }`}
                  onMouseEnter={() => inspectorStore.getState().setHover(g.ghostId)}
                  onMouseLeave={() => inspectorStore.getState().setHover(null)}
                  onClick={() => openInspectorFor(g.ghostId, onSelect)}
                  onDoubleClick={() => zoomToGhost(g.ghostId, onSelect)}
                >
                  <div className="roster__row-top">
                    <span className="roster__name">
                      {g.attention?.needs && (
                        <span
                          className="roster__attn"
                          title={safeSurfaceText(g.attention.reason, "needs operator")}
                          aria-label="needs operator"
                        >
                          !
                        </span>
                      )}
                      {g.name}
                    </span>
                    <span className="roster__behavior">{g.behavior}</span>
                  </div>
                  <div className="roster__row-mid">
                    <span className="roster__state">{ghostStateLabel(g.state)}</span>
                    {g.records > 0 && <span className="roster__records">{g.records} rec</span>}
                  </div>
                  {g.task && <div className="roster__task" title={g.task}>{g.task}</div>}
                  <div className="roster__bar">
                    <div className="roster__bar-fill" style={{ width: pct(g.progress) }} />
                  </div>
                </button>
                {onManage && (
                  <button
                    type="button"
                    className="roster__dismiss"
                    aria-label={`dismiss ${g.name}`}
                    title="dismiss (cancel this ghost)"
                    onClick={() => onManage({ command: "cancel", ghostId: g.ghostId })}
                  >
                    ×
                  </button>
                )}
                {onManage && (
                  <div className="roster__manage" role="group" aria-label={`manage ${g.name}`}>
                    <button
                      type="button"
                      className="roster__manage-btn"
                      onClick={() => onManage({ command: "pause", ghostId: g.ghostId })}
                    >
                      pause
                    </button>
                    <button
                      type="button"
                      className="roster__manage-btn"
                      onClick={() => onManage({ command: "resume", ghostId: g.ghostId })}
                    >
                      resume
                    </button>
                    {retargetTo && (
                      <button
                        type="button"
                        className="roster__manage-btn"
                        title={`retarget to ${retargetTo.label}`}
                        onClick={() =>
                          onManage({
                            command: "retarget",
                            ghostId: g.ghostId,
                            section: retargetTo.id,
                          })
                        }
                      >
                        {`retarget→${retargetTo.label}`}
                      </button>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </section>
        );
      })}
    </div>
  );
}
