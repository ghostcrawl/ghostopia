// ghostopia web — React chrome mounting the PixiJS canvas + a minimal HUD.
//
// React renders CHROME ONLY. The world animates in the PixiJS ticker (render
// loop) reading the Zustand store — there is NO React re-render per frame.
// The HUD subscribes to the same store for a live ghost count / selection. This
// file (and the whole app) imports NO GhostCrawl SDK, NO Python package, NO key.

import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import {
  createRenderLoop,
  useWorldStore,
  type EditorHooks,
  type RenderLoopHandle,
  type WorldMapData,
} from "@ghostopia/ghost-renderer";

import { EditorMode } from "./editor/EditorMode";
import { buildEditorView, editorStore } from "./editor/editorStore";
import { draftFromMapData, draftToMapData, wireToDraft } from "./editor/mapio";
import type { Footprints } from "./editor/tools";
import { loadWorldAssets } from "./world/assets";
import { SimClient } from "./simClient";
import { startLiveClient, type LiveClientHandle } from "./liveClient";
import { resolvedWsUrl, tokenUrlFrom } from "./wsClient";
import { InspectorPanel, openInspectorFor } from "./inspector/InspectorPanel";
import { StatusPopup } from "./inspector/StatusPopup";
import { inspectorStore } from "./inspector/inspectorStore";
import { GhostRoster } from "./hud/GhostRoster";
import { ConnectionIndicator } from "./hud/ConnectionIndicator";
import { groupBySection, rosterStore } from "./hud/rosterStore";
import { aggregateThroughput, resultsStore } from "./hud/resultsStore";
import { ZoomControls } from "./hud/ZoomControls";
import { SettingsPanel } from "./hud/SettingsPanel";
import { soundboard } from "./sound/soundboardInstance";
import { MissionForm } from "./hud/MissionForm";
import { DepartmentsPanel, type SectionSaveResult } from "./hud/DepartmentsPanel";
import { AdvancedDepartments } from "./hud/AdvancedDepartments";
import { anyWorkforceGhost } from "./hud/workforce";
import { Dashboard } from "./hud/Dashboard";
import { DataGraveyard } from "./hud/DataGraveyard";
import { DepartmentResults } from "./hud/DepartmentResults";
import { sectionFocusStore } from "./hud/sectionFocusStore";
import { handleSectionClick, isDepartmentSection } from "./hud/departmentGate";
import { surfaceLabel } from "./hud/surfaceVocab";
import { SectionsPanel } from "./hud/SectionsPanel";
import { GhostInspector } from "./hud/GhostInspector";
import { DiagnosticsPanel } from "./hud/DiagnosticsPanel";
import { HudShell } from "./hud/HudShell";

/** The real-work section roles for the HUD legend (dot colour comes from the palette).
 *  Dropped the "home" legend row — the "home" concept is gone (graves
 *  scatter as placeable spawn points, no designated home); the legend names only real
 *  GhostCrawl-work zones. */
const LEGEND: Array<{ id: string; label: string }> = [
  { id: "horror-books", label: "horror books" },
  { id: "mystery-books", label: "mystery books" },
  { id: "spooky-masks", label: "spooky masks" },
  { id: "spooky-costumes", label: "spooky costumes" },
  // The map is departments-only — the example.com stage utility zones
  // (research/extraction/verify/canvas/error) are gone. resting is intentionally omitted:
  // the graveyard land itself is the resting area (no zone).
];

function hex6(color: number): string {
  return `#${(color & 0xffffff).toString(16).padStart(6, "0")}`;
}

/** 196: on a phone the dock is a short bottom sheet, so the always-open docked panels
 *  (dashboard / data graveyard / roster) start COLLAPSED — the sheet opens short and the world
 *  stays visible behind it. Thin frontend: a single matchMedia read at module load, no deps. */
const IS_MOBILE =
  typeof window !== "undefined" &&
  typeof window.matchMedia === "function" &&
  window.matchMedia("(max-width: 640px)").matches;

function Hud({ sectionTints }: { sectionTints: Record<string, number> }): JSX.Element {
  const ghostCount = useStore(useWorldStore, (s) => Object.keys(s.ghosts).length);
  const zoom = useStore(useWorldStore, (s) => s.camera.zoom);
  const showLabels = useStore(useWorldStore, (s) => s.showLabels);
  const followEnabled = useStore(useWorldStore, (s) => s.followEnabled);
  return (
    <div className="hud">
      <div className="hud__title">ghostopia</div>
      {/* The in-app leg of the "Powered by GhostCrawl" attribution — a quiet
          sub-line under the masthead linking the product site. Surface-safe (GhostCrawl allowed);
          .hud is pointer-events:none, so the link re-enables its own pointer-events. */}
      <a
        className="hud__poweredby"
        href="https://ghostcrawl.io"
        target="_blank"
        rel="noopener noreferrer"
      >
        Powered by GhostCrawl
      </a>
      <div className="hud__stat">ghosts: {ghostCount}</div>
      <div className="hud__stat">zoom: {zoom.toFixed(2)}×</div>
      <ul className="legend">
        {LEGEND.filter((r) => sectionTints[r.id] !== undefined).map((r) => (
          <li className="legend__row" key={r.id}>
            <span className="legend__dot" style={{ background: hex6(sectionTints[r.id]) }} />
            {r.label}
          </li>
        ))}
        {/* S1 affordance: only department plots are clickable result repositories. */}
        <li className="legend__row legend__row--hint" key="department-hint">
          department — click for findings
        </li>
      </ul>
      <div className="hud__toggles">
        <button
          type="button"
          className={`hud__toggle${followEnabled ? " hud__toggle--on" : ""}`}
          aria-pressed={followEnabled}
          onClick={() => useWorldStore.getState().setFollowEnabled(!followEnabled)}
        >
          {followEnabled ? "● follow" : "○ follow"}
        </button>
        <button
          type="button"
          className={`hud__toggle${showLabels ? " hud__toggle--on" : ""}`}
          aria-pressed={showLabels}
          onClick={() => useWorldStore.getState().setShowLabels(!showLabels)}
        >
          {showLabels ? "● labels" : "○ labels"}
        </button>
      </div>
      {/* W5/S6: the "drag to pan" segment is remapped display-only via surfaceLabel — the camera
          instruction stays 100% clear ("drag to drift the view"), the technical term "pan" goes. */}
      <div className="hud__hint">{surfaceLabel("drag to pan")} · scroll to zoom · ↑↓ cycle · esc deselect</div>
    </div>
  );
}

export function App(): JSX.Element {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const [sectionTints, setSectionTints] = useState<Record<string, number>>({});
  const [simMode, setSimMode] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [editorOn, setEditorOn] = useState(false);
  // The last section.save/remove result — drives the Departments panel reject line.
  const [sectionSaveResult, setSectionSaveResult] = useState<SectionSaveResult | null>(null);
  // the server-owned set of ADVANCED real-retail departments currently switched on
  // (relayed on every workforce.advanced ack) — drives the opt-in toggle UI.
  const [advancedEnabled, setAdvancedEnabled] = useState<string[]>([]);
  // 195: the workforce is "running" whenever any workforce/department ghost is in the world —
  // derived from the live roster (reconnect-safe: no separate client state to drift). Drives the
  // Run ⇄ Stop toggle so the operator starts a working wave and stops it (dematerialize).
  // match the server's AUTHORITATIVE workforce set (_has_live_workforce /
  // _stop_workforce), which is workforce-* / dept-* AND the background baton stage-* ghosts —
  // via the shared `anyWorkforceGhost` predicate. If the featured dept-* ghosts are transiently
  // absent (between loop cycles) while stage-* ghosts are live, omitting stage- would flip the
  // Run⇄Stop toggle + onboarding CTA to the wrong state (overlay over a running workforce).
  const workforceRunning = useStore(useWorldStore, (s) => anyWorkforceGhost(Object.keys(s.ghosts)));
  // (S5, W7 touchpoint 1): the empty-state onboarding CTA is shown in Live mode when no
  // department has run yet AND zero findings have landed across every department — the first-load
  // "summon the workforce" teaching moment. Derived from the live results store (reconnect-safe).
  const previewCount = useStore(resultsStore, (s) => s.preview.length);
  const recordCount = useStore(resultsStore, (s) => aggregateThroughput(s.sections).records);
  const simClientRef = useRef<SimClient | null>(null);
  const liveClientRef = useRef<LiveClientHandle | null>(null);
  // Graveyard Builder: the active (live) map + cached assets so the render loop can be
  // rebuilt when a validated `map.save`/`map.reset` rebroadcasts a new world (assets stay loaded).
  const [activeMap, setActiveMap] = useState<WorldMapData | null>(null);
  const activeMapRef = useRef<WorldMapData | null>(null);
  const assetsRef = useRef<Awaited<ReturnType<typeof loadWorldAssets>> | null>(null);
  const footprintsRef = useRef<Footprints>({});
  const sectionTintsNumRef = useRef<Record<string, number>>({});
  const handleRef = useRef<RenderLoopHandle | null>(null);

  // Stable editor hooks handed to the render loop: draw the overlay from the editor store, and
  // route canvas taps/hover to the store's tool ops. Reads module-global state, so it never
  // needs to be recreated (no loop churn on an editor toggle).
  const editorHooksRef = useRef<EditorHooks>({
    getView: () => buildEditorView(sectionTintsNumRef.current),
    onTile: (x, y, button) => editorStore.getState().interactTile({ x, y }, button),
    onHover: (x, y) => editorStore.getState().setHoverTile({ x, y }),
  });

  // Canvas click-to-select: route a ghost sprite click to the SAME path the roster row uses.
  // The render loop is created once; it calls this ref so it always reaches the current client.
  // Canvas click-to-pet: route a critter click to the current client's `critter.pet` verb so
  // the server acks a heart/spark flash. Like selectRef, the loop calls this ref so it always
  // reaches whichever client (sim/live) is active.
  const petRef = useRef<(id: string) => void>(() => {});
  petRef.current = (id: string): void => {
    if (liveClientRef.current) liveClientRef.current.petCritter(id);
    else if (simClientRef.current) simClientRef.current.petCritter(id);
  };

  // Canvas click-to-inspect-a-department (196 / S1): a click that misses every
  // ghost/critter but lands on a DEPARTMENT plot opens that department's findings card (toggle:
  // re-clicking the open one closes it). A non-department section (resting) or bare ground is
  // intentional silence — a no-op (S1). The department gate reads the server-authoritative
  // catalog `kind` tag. Ref-indirected like petRef/selectRef so the once-built render loop
  // always reaches the current handler.
  const sectionRef = useRef<(id: string | null) => void>(() => {});
  sectionRef.current = (id: string | null): void => {
    handleSectionClick(id);
  };

  const selectRef = useRef<(id: string | null) => void>(() => {});
  selectRef.current = (id: string | null): void => {
    if (liveClientRef.current) {
      const send = (i: string | null): void => liveClientRef.current?.selectGhost(i);
      if (id) openInspectorFor(id, send);
      else {
        inspectorStore.getState().closeInspector();
        send(null);
      }
    } else if (simClientRef.current) {
      simClientRef.current.selectGhost(id);
    } else {
      useWorldStore.getState().selectGhost(id);
    }
  };

  // Simulated mode: let the server drive the world over the authed WS (spawns + visual
  // commands). Toggling off clears the sim ghosts back to an EMPTY graveyard — every ghost
  // is a real GhostCrawl-backed session, so there is no client-invented seed to restore.
  useEffect(() => {
    const store = useWorldStore.getState();
    if (!simMode) {
      for (const id of Object.keys(store.ghosts)) store.removeGhost(id);
      return;
    }
    for (const id of Object.keys(store.ghosts)) store.removeGhost(id);
    const client = new SimClient();
    simClientRef.current = client;
    client.connect();
    return () => {
      client.disconnect();
      simClientRef.current = null;
    };
  }, [simMode]);

  // Live mode (STAGE 3): open the authed WS live client. Submitting a mission runs ONE real
  // GhostCrawl session server-side; the ghost animates the real work over the SAME store path
  // as sim. Toggling off tears the socket down and clears back to an EMPTY graveyard — no
  // client-invented seed to restore (every ghost is a real GhostCrawl-backed session).
  useEffect(() => {
    const store = useWorldStore.getState();
    if (!liveMode) {
      if (liveClientRef.current) {
        liveClientRef.current.stop();
        liveClientRef.current = null;
      }
      inspectorStore.getState().closeInspector();
      inspectorStore.getState().setHover(null);
      sectionFocusStore.getState().clear();
      if (!simMode) {
        for (const id of Object.keys(store.ghosts)) store.removeGhost(id);
      }
      return;
    }
    for (const id of Object.keys(store.ghosts)) store.removeGhost(id);
    // Authenticate against the self-hosted backend via its real `/token` route. A fresh
    // tab has no token and GETs one; a tab whose baked VITE_GHOSTOPIA_WS_TOKEN predates a restart
    // re-fetches a fresh one instead of reconnecting forever with the stale one (empty world). The
    // provider always points at `/token` derived from the gateway URL — a blank/failed fetch keeps
    // the last good token, so a backend without the route is never worse than the static behavior.
    const tokenUrl = tokenUrlFrom(resolvedWsUrl());
    const client = startLiveClient({
      tokenUrl,
      // A validated map.save/reset rebroadcasts the authoritative world. Apply it to
      // the live render loop (rebuild from the new map) + re-base the editor draft if editing.
      onWorldSnapshot: (map) => {
        try {
          const draft = wireToDraft(map);
          const md = draftToMapData(draft);
          setActiveMap(md);
          activeMapRef.current = md;
          // swap the live world IN PLACE (rebuild static content only — no Pixi re-init).
          handleRef.current?.reloadMap(md);
          const es = editorStore.getState();
          if (es.active && assetsRef.current) {
            es.enter(draft, footprintsRef.current, assetsRef.current.catalog);
          }
        } catch {
          /* a malformed snapshot is ignored — the live map stays as-is */
        }
      },
      onMapSaved: (ok, reason) => {
        editorStore.setState({ saveResult: ok ? "saved ✓ live" : `rejected: ${reason ?? "invalid map"}` });
      },
      // A section.save/remove result — the Departments panel surfaces a reject reason
      // and keeps the form; on ok it clears back to "new" (the server rebroadcasts the catalog).
      onSectionSaved: (ok, reason) => setSectionSaveResult({ ok, reason }),
      // Keep the advanced opt-in toggles in sync with the server-owned enabled set.
      onAdvancedToggled: (advanced) => setAdvancedEnabled(advanced),
    });
    liveClientRef.current = client;
    return () => {
      client.stop();
      liveClientRef.current = null;
    };
  }, [liveMode, simMode]);

  // AUTO-LIVE (195): the persistent operator app boots the app straight into Live
  // mode so the auto-run workforce world (departments working, ghosts roaming) is alive ON LOAD
  // — no "Live mode" click needed. Gated to `VITE_GHOSTOPIA_AUTOLIVE` so the E2E harness (which
  // drives modes explicitly) is unaffected. Runs once on mount.
  useEffect(() => {
    const env = (import.meta as unknown as { env?: Record<string, string> }).env;
    const flag = (env?.VITE_GHOSTOPIA_AUTOLIVE ?? "").toString().toLowerCase();
    if (["1", "true", "yes", "on"].includes(flag)) setLiveMode(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Global keyboard UX: ↑/↓ cycle the selected ghost across the roster order,
  // Esc deselects. Ignored while typing in the mission form (an input/textarea/select is focused).
  useEffect(() => {
    const orderedIds = (): string[] =>
      groupBySection(rosterStore.getState().ghosts).flatMap((grp) =>
        grp.rows.map((r) => r.ghostId),
      );
    const onKeyDown = (e: KeyboardEvent): void => {
      const el = document.activeElement;
      if (el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName)) return;
      if (e.key === "Escape") {
        selectRef.current(null);
        return;
      }
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      const ids = orderedIds();
      if (ids.length === 0) return;
      e.preventDefault();
      const cur = useWorldStore.getState().selectedGhostId;
      const idx = cur ? ids.indexOf(cur) : -1;
      const step = e.key === "ArrowDown" ? 1 : -1;
      const next = idx < 0 ? (step === 1 ? 0 : ids.length - 1) : (idx + step + ids.length) % ids.length;
      selectRef.current(ids[next]);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  // Autoplay-policy safe: resume the AudioContext on the FIRST user gesture anywhere (a click /
  // key / touch), so an operator who enabled sound in a prior session hears cues without a
  // second click. One-shot — removed after the first gesture.
  useEffect(() => {
    const unlock = (): void => {
      soundboard.unlock();
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => {
      window.removeEventListener("pointerdown", unlock);
      window.removeEventListener("keydown", unlock);
    };
  }, []);

  // Load the world assets ONCE (atlas/map/catalog/palettes). The render loop is (re)built by
  // the effect below from `activeMap` so a validated map.save/reset can swap the live world
  // without reloading assets. Footprints for the editor are derived from the prop catalog.
  useEffect(() => {
    let cancelled = false;
    let cinematicCancel: (() => void) | undefined;
    (async () => {
      try {
        const assets = await loadWorldAssets();
        if (cancelled) return;
        assetsRef.current = assets;
        setSectionTints(assets.sectionTints);
        // build a name->number section tint map for the editor overlay (plot fills).
        const nums: Record<string, number> = {};
        for (const [k, v] of Object.entries(assets.sectionTints)) if (typeof v === "number") nums[k] = v;
        sectionTintsNumRef.current = nums;
        // catalog footprints for the editor (catalog_id -> {w,h}).
        const fps: Footprints = {};
        for (const [id, def] of Object.entries(assets.catalog.props)) fps[id] = { w: def.footprint.w, h: def.footprint.h };
        footprintsRef.current = fps;
        // The world starts EMPTY: no client-invented seed ghosts. Every ghost is a real
        // GhostCrawl-backed session delivered by the server (Simulated/Live). On load the app
        // either auto-Lives into the real workforce (VITE_GHOSTOPIA_AUTOLIVE) or shows the clean
        // empty graveyard + "summon the workforce" onboarding CTA.
        setActiveMap(assets.mapData);
        activeMapRef.current = assets.mapData;
        if (!canvasRef.current) return;

        // Create the render loop ONCE. A later validated map.save/reset swaps the world IN PLACE
        // via handle.reloadMap (rebuilding only the static content) — NEVER a full Pixi re-init,
        // which wedges headless WebGL on the same canvas.
        const handle = await createRenderLoop({
          canvas: canvasRef.current,
          mapData: assets.mapData,
          atlas: assets.atlas,
          book: assets.book,
          paletteBook: assets.paletteBook,
          catalog: assets.catalog,
          sectionTints: assets.sectionTints,
          theme: assets.theme,
          onSelectGhost: (id) => selectRef.current(id),
          onPetCritter: (id) => petRef.current(id),
          onSelectSection: (id) => sectionRef.current(id),
          // S1 hover affordance: the render loop asks whether the hovered plot is a clickable
          // department so it can swap the stage cursor (pointer over a department, grab elsewhere).
          isDepartmentSection: (id) => isDepartmentSection(id),
          editor: editorHooksRef.current,
        });
        if (cancelled) {
          handle.destroy();
          return;
        }
        handleRef.current = handle;

        // Startup cinematic: a one-shot pan from the Crypt/entry to the hub, skipped
        // under reduced motion + cancelled on the first interaction.
        const md = assets.mapData;
        const ts = md.tileSize;
        const hub = { x: (md.width * ts) / 2, y: (md.height * ts) / 2, zoom: 2.5 };
        const crypt = md.regions["crypt"];
        const reduce =
          typeof window !== "undefined" && typeof window.matchMedia === "function"
            ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
            : false;
        if (reduce || !crypt) {
          useWorldStore.getState().setCamera(hub);
        } else {
          const from = { x: (crypt.x + crypt.w / 2) * ts, y: (crypt.y + crypt.h / 2) * ts, zoom: 3.4 };
          useWorldStore.getState().setCamera(from);
          const DURATION = 1900;
          let raf = 0;
          let start = 0;
          let done = false;
          const cancel = (): void => {
            done = true;
            if (raf) cancelAnimationFrame(raf);
            window.removeEventListener("pointerdown", cancel);
            window.removeEventListener("wheel", cancel);
            window.removeEventListener("keydown", cancel);
          };
          cinematicCancel = cancel;
          const ease = (t: number): number => 1 - Math.pow(1 - t, 3);
          const step = (now: number): void => {
            if (done || cancelled) return;
            if (!start) start = now;
            const t = Math.min(1, (now - start) / DURATION);
            const k = ease(t);
            useWorldStore.getState().setCamera({
              x: from.x + (hub.x - from.x) * k,
              y: from.y + (hub.y - from.y) * k,
              zoom: from.zoom + (hub.zoom - from.zoom) * k,
            });
            if (t < 1) raf = requestAnimationFrame(step);
            else cancel();
          };
          window.addEventListener("pointerdown", cancel);
          window.addEventListener("wheel", cancel);
          window.addEventListener("keydown", cancel);
          raf = requestAnimationFrame(step);
        }
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof Error ? err.message : String(err));
        setStatus("error");
      }
    })();
    return () => {
      cancelled = true;
      cinematicCancel?.();
      handleRef.current?.destroy();
      handleRef.current = null;
    };
  }, []);

  // Enter/leave the Graveyard Builder: on enter, clone a DRAFT from the live map + arm the store
  // (+ ask the server for the authoritative snapshot when connected); on leave, drop the draft.
  useEffect(() => {
    activeMapRef.current = activeMap;
  }, [activeMap]);

  useEffect(() => {
    const assets = assetsRef.current;
    const map = activeMapRef.current;
    // Keyed on `editorOn` ONLY (not activeMap): a save/reset changes activeMap, but the editor
    // must not re-enter mid-session — onWorldSnapshot re-bases the draft to the authoritative map.
    if (editorOn && map && assets) {
      editorStore.getState().enter(draftFromMapData(map), footprintsRef.current, assets.catalog);
    } else {
      editorStore.getState().exit();
    }
  }, [editorOn]);

  return (
    <div className="app">
      <canvas ref={canvasRef} className="stage" />
      <Hud sectionTints={sectionTints} />
      <ConnectionIndicator />
      <ZoomControls />
      <SettingsPanel />
      <button
        type="button"
        className="mode-toggle"
        aria-pressed={simMode}
        onClick={() => setSimMode((v) => !v)}
      >
        {simMode ? "● Simulated (live)" : "○ Simulated mode"}
      </button>
      <button
        type="button"
        className="mode-toggle mode-toggle--live"
        aria-pressed={liveMode}
        onClick={() => setLiveMode((v) => !v)}
      >
        {liveMode ? "● Live (real session)" : "○ Live mode"}
      </button>
      <button
        type="button"
        className="mode-toggle mode-toggle--editor"
        aria-pressed={editorOn}
        onClick={() => setEditorOn((v) => !v)}
      >
        {editorOn ? "● Graveyard Builder" : "○ Graveyard Builder"}
      </button>
      {editorOn && (
        <EditorMode
          onSave={() => {
            const wire = editorStore.getState().toWire();
            if (!wire) return;
            if (liveClientRef.current) liveClientRef.current.saveMap(wire as unknown as Record<string, unknown>);
            else editorStore.setState({ saveResult: "enter Live mode to save to the server" });
          }}
          onReset={() => {
            if (liveClientRef.current) liveClientRef.current.resetMap();
            else editorStore.setState({ saveResult: "enter Live mode to reset the live map" });
          }}
          onExit={() => setEditorOn(false)}
        />
      )}
      {liveMode && (
        <>
          {/* ONE docked, non-overlapping, collapsible control shell — the panels no
              longer stack on each other or cover the in-world section labels. A prominent
              "Run workforce" button is the obvious first action. */}
          <HudShell
            topActions={
              <button
                type="button"
                className="hud-shell__workforce"
                aria-label={workforceRunning ? "dismiss the departments" : "summon the departments"}
                data-running={workforceRunning ? "true" : "false"}
                onClick={() =>
                  workforceRunning
                    ? liveClientRef.current?.stopWorkforce()
                    : liveClientRef.current?.runWorkforce()
                }
              >
                {workforceRunning
                  ? "■ dismiss the departments"
                  : `▶ ${surfaceLabel("Run workforce")} — the spooky workforce`}
              </button>
            }
            panels={[
              {
                id: "mission",
                title: "mission",
                defaultOpen: true,
                node: (
                  <MissionForm
                    onSubmit={(mission) => liveClientRef.current?.submitMissionFanout(mission)}
                  />
                ),
              },
              {
                id: "sections",
                title: "sections — add / remove ghosts",
                defaultOpen: true,
                node: (
                  <SectionsPanel
                    onManage={(cmd) => liveClientRef.current?.manageGhost(cmd)}
                    onSpawn={(section) => liveClientRef.current?.spawnGhost(section)}
                    onDespawn={(id) => liveClientRef.current?.despawnGhost(id)}
                  />
                ),
              },
              {
                id: "departments",
                title: "departments — what each team scrapes",
                defaultOpen: false,
                node: (
                  <>
                    <DepartmentsPanel
                      onSave={(section) => liveClientRef.current?.saveSection(section)}
                      onRemove={(id) => liveClientRef.current?.removeSection(id)}
                      saveResult={sectionSaveResult}
                    />
                    {/* the opt-in ADVANCED real-retail departments (off by default). */}
                    <AdvancedDepartments
                      enabled={advancedEnabled}
                      onToggle={(id, enabled) => liveClientRef.current?.enableAdvanced(id, enabled)}
                    />
                  </>
                ),
              },
              {
                id: "inspector",
                title: "selected ghost — assign / send",
                defaultOpen: true,
                node: (
                  <GhostInspector onManage={(cmd) => liveClientRef.current?.manageGhost(cmd)} />
                ),
              },
              { id: "dashboard", title: "dashboard", defaultOpen: !IS_MOBILE, node: <Dashboard /> },
              {
                id: "diagnostics",
                title: "diagnostics",
                defaultOpen: false,
                node: <DiagnosticsPanel />,
              },
              { id: "data", title: "data graveyard", defaultOpen: !IS_MOBILE, node: <DataGraveyard /> },
              {
                id: "roster",
                title: "roster",
                defaultOpen: !IS_MOBILE,
                node: (
                  <GhostRoster
                    onSelect={(id) => liveClientRef.current?.selectGhost(id)}
                    onManage={(cmd) => liveClientRef.current?.manageGhost(cmd)}
                  />
                ),
              },
            ]}
          />
          <StatusPopup />
          <InspectorPanel onSelect={(id) => liveClientRef.current?.selectGhost(id)} />
          {/* 196: click a department plot on the map → its real findings float up here. */}
          <DepartmentResults />
        </>
      )}
      {/* (S5): the empty-state "summon the workforce" onboarding CTA. Reuses .overlay
          (position:absolute; inset:0; grid; place-items:center; pointer-events:none) — the button
          is the SOLE pointer-events:auto child, so the canvas stays draggable behind it. No modal,
          no wizard, no dismissal persistence (S5 scope). Fires the existing runWorkforce trigger. */}
      {liveMode && !workforceRunning && previewCount === 0 && recordCount === 0 && (
        <div className="overlay overlay--onboard">
          <div className="overlay__cta">
            <div className="overlay__cta-head">no ghosts on shift yet</div>
            <div className="overlay__cta-body">
              summon the workforce to start bringing back real product data
            </div>
            <button
              type="button"
              className="editor__btn editor__btn--save overlay__cta-btn"
              onClick={() => liveClientRef.current?.runWorkforce()}
            >
              summon the workforce
            </button>
          </div>
        </div>
      )}
      {status === "loading" && <div className="overlay">summoning the graveyard…</div>}
      {status === "error" && <div className="overlay overlay--error">render error: {error}</div>}
    </div>
  );
}
