// ghostopia web — the Live-mode client (STAGE 3: real GhostCrawl sessions).
//
// A DROP-IN for `simClient`: the SAME store-applying path (ghost.spawned upsert +
// ghost.command walk/face/anim/say driving the store, with client-side rAF
// interpolation along the server's A* path) — only the SOURCE differs. Here the envelopes
// are REAL: the Python server ran one real GhostCrawl session (server-side SDK, no key here)
// and streamed the ghost's real work over the authed WS. The renderer is unchanged; the
// ghost animates real navigation + real extracted records.
//
// A mission is submitted as `mission.submit {target_name, url}` — the client sends ONLY a
// target NAME + a URL, never a key. This file imports NO backend package and NO SDK.

import {
  useWorldStore,
  type Facing,
  type GhostAttention,
  type GhostState,
  type GhostWorkKind,
  type Point,
} from "@ghostopia/ghost-renderer";

import { catalogStore, type CatalogBehavior, type CatalogSection } from "./hud/catalogStore";
import { diagnosticsStore } from "./hud/diagnosticsStore";
import { metricsStore } from "./hud/metricsStore";
import { resultsStore } from "./hud/resultsStore";
import { rosterStore } from "./hud/rosterStore";
import { inspectorStore } from "./inspector/inspectorStore";
import { overlayGlyph } from "./overlayGlyph";
import { soundboard } from "./sound/soundboardInstance";
import { isSurfaceSafe, safeSurfaceText } from "./surfaceSafe";
import { applyWorldEnvelope } from "./worldEnvelope";
import { PROTOCOL_VERSION, WsClient, type Envelope } from "./wsClient";

/**
 * Build a re-fetching token provider from the backend's `/token` endpoint URL. Returns
 * undefined when no url is given (sim). The provider GETs the endpoint and returns the fresh
 * `token`; any failure returns null so the WsClient keeps its last good token — a fresh tab
 * authenticates the self-hosted backend, and a stale-tab reconnect after a restart self-heals.
 */
function makeTokenProvider(tokenUrl?: string): (() => Promise<string | null>) | undefined {
  if (!tokenUrl) return undefined;
  return async (): Promise<string | null> => {
    try {
      const resp = await fetch(tokenUrl, { cache: "no-store" });
      if (!resp.ok) return null;
      const body = (await resp.json()) as { token?: unknown };
      return typeof body.token === "string" && body.token.length > 0 ? body.token : null;
    } catch {
      return null;
    }
  };
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}
function num(v: unknown, fallback: number): number {
  return typeof v === "number" ? v : fallback;
}

function asPoint(v: unknown): Point | null {
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  if (typeof o.x !== "number" || typeof o.y !== "number") return null;
  return { x: o.x, y: o.y };
}

const FACINGS = new Set<Facing>(["s", "se", "e", "ne", "n", "nw", "w", "sw"]);
/** Validate a server-authored 8-way facing string off a `face` command; null when absent/invalid. */
function asFacing(v: unknown): Facing | null {
  return typeof v === "string" && FACINGS.has(v as Facing) ? (v as Facing) : null;
}

/** Parse an `attention` flag off the status envelope; null when absent/not-needed. */
export function parseAttention(v: unknown): GhostAttention | null {
  if (typeof v !== "object" || v === null) return null;
  const o = v as Record<string, unknown>;
  if (o.needs !== true) return null;
  // Boundary: the server already curates the reason, but the client guards it too so a
  // raw/vendor reason can never reach the roster "!" tooltip (renders "needs operator" instead).
  const reason = typeof o.reason === "string" ? safeSurfaceText(o.reason, "needs operator") : null;
  return { needs: true, reason };
}

/** anim name -> the ghost state whose clip the GhostSprite plays (incl. per-kind work). */
const ANIM_STATE: Record<string, GhostState> = {
  work: "EXTRACTING",
  "work.navigating": "NAVIGATING",
  "work.searching": "SEARCHING",
  "work.reading": "READING",
  "work.scrolling": "SCROLLING",
  "work.extracting": "EXTRACTING",
  error: "ERROR",
  success: "COMPLETED",
};

interface WalkAnim {
  path: Point[];
  seg: number;
  segT: number;
}

/**
 * Applies server-authoritative envelopes to the world store and interpolates walks on a rAF
 * loop — the EXACT store-applying wiring `simClient` uses, so the sim and the live sessions
 * animate through one identical path (only the envelope source differs).
 */
class StoreApplier {
  private readonly walkSpeed: number;
  private raf = 0;
  private lastTs = 0;
  private closed = false;
  private readonly walks = new Map<string, WalkAnim>();

  constructor(walkSpeed: number) {
    this.walkSpeed = walkSpeed;
  }

  start(): void {
    this.closed = false;
    this.lastTs = performance.now();
    const step = (now: number): void => {
      const dt = Math.min(0.1, (now - this.lastTs) / 1000);
      this.lastTs = now;
      this.advanceWalks(dt);
      if (!this.closed) this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }

  stop(): void {
    this.closed = true;
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.walks.clear();
  }

  apply(env: Envelope): void {
    // STAGE-7 dashboard: count real nav/session/retry/error signals off the same stream.
    this.countMetrics(env);
    // Tap the stream for optional sound cues (off unless the operator enabled it).
    soundboard.handle(env.type, (env.payload ?? {}) as Record<string, unknown>, env.ghost_id);
    // World envelopes (critters + reactive props) route to the shared world applier.
    if (applyWorldEnvelope(env.type, (env.payload ?? {}) as Record<string, unknown>)) return;
    if (env.type === "ghost.spawned") this.applySpawn(env);
    else if (env.type === "ghost.despawned") this.applyDespawn(env);
    else if (env.type === "ghost.command") this.applyCommand(env);
    // STAGE-5 roster status: the frame-FREE cadence keeps every ghost's roster row fresh
    // (state/behavior/task/progress/records). No frame stream rides this envelope.
    else if (env.type === "ghost.status_changed") this.applyRosterStatus(env);
    // An applied management command (reassign / assign_section) authoritatively moved
    // the ghost's section — reflect it in the roster + world IMMEDIATELY (the frame-free status
    // poll skips the SELECTED ghost, so we cannot wait for it to carry the new section).
    else if (env.type === "management.applied") this.applyManagement(env);
    // STAGE-7 results: mission progress + preview + throughput drive the Data Graveyard +
    // dashboard, computed by the server from the persisted SQLite result store.
    else if (env.type === "mission.created" || env.type === "result.mission_progress")
      this.applyResults(env);
    // STAGE-7 management catalog: server-relayed behaviors + sections for the dropdowns.
    else if (env.type === "catalog.behaviors" || env.type === "catalog.sections")
      this.applyCatalog(env);
    // A ghost's REAL scraped record → its inspector Activity feed (keyed by ghost, so it only
    // shows for whichever ghost is open). This is what makes a stateless ghost's inspector show
    // real activity — its finds — instead of the empty "still gathering" placeholder.
    else if (env.type === "result.scraped" || env.type === "result.record_extracted")
      this.applyScrapedRecord(env);
    // Diagnostics: a frame-free REAL system-health snapshot (pool/governor/sections).
    else if (env.type === "diagnostics.system")
      diagnosticsStore.getState().applySystem((env.payload ?? {}) as Record<string, unknown>);
    // STAGE-4 inspector envelopes: the server relays the SELECTED ghost's real frames +
    // status; errors/records accrue per ghost. The client never calls GhostCrawl — it draws
    // only what the server relayed.
    else this.applyInspector(env);
  }

  /** Count the REAL dashboard signals off the server stream. */
  private countMetrics(env: Envelope): void {
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const m = metricsStore.getState();
    switch (env.type) {
      case "browser.navigate":
        m.countNavigate(env.ts);
        break;
      case "browser.session_opened":
        m.countSession();
        break;
      case "task.retry":
        m.countRetry();
        m.countError(str(p.code) ?? "", str(p.visual));
        break;
      case "browser.error":
        m.countError(str(p.code) ?? "", str(p.visual));
        break;
      default:
        break;
    }
  }

  /** Route STAGE-7 result envelopes into the results store (Data Graveyard + dashboard). */
  private applyResults(env: Envelope): void {
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const store = resultsStore.getState();
    if (env.type === "mission.created") {
      const id = str(p.mission_id);
      if (id) store.createMission(id, str(p.title) ?? id, num(p.total, 0));
    } else {
      store.applyProgress(p);
    }
  }

  /** Route STAGE-7 catalog envelopes into the catalog store (management dropdowns). */
  private applyCatalog(env: Envelope): void {
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const store = catalogStore.getState();
    if (env.type === "catalog.behaviors" && Array.isArray(p.behaviors)) {
      store.setBehaviors(p.behaviors as CatalogBehavior[]);
    } else if (env.type === "catalog.sections" && Array.isArray(p.sections)) {
      store.setSections(p.sections as CatalogSection[]);
    }
  }

  /** Reflect an applied section move (reassign / assign_section) at once. */
  private applyManagement(env: Envelope): void {
    const gid = env.ghost_id;
    if (!gid) return;
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const section = str(p.section);
    if (!section) return;
    const cur = rosterStore.getState().ghosts[gid];
    if (cur) rosterStore.getState().applyStatus({ ...cur, section });
    if (useWorldStore.getState().ghosts[gid]) {
      useWorldStore.getState().upsertGhost({ id: gid, section });
    }
  }

  /** Route a frame-free `ghost.status_changed` into the roster store + coarse world state. */
  private applyRosterStatus(env: Envelope): void {
    const gid = env.ghost_id;
    if (!gid) return;
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const attention = parseAttention(p.attention);
    rosterStore.getState().applyStatus({
      ghostId: gid,
      name: str(p.name) ?? gid,
      section: str(p.section) ?? "",
      behavior: str(p.behavior) ?? "",
      state: str(p.state) ?? "IDLE",
      task: str(p.task),
      currentUrl: str(p.current_url),
      progress: num(p.progress, 0),
      records: num(p.records, 0),
      attention,
    });
    // liveness: mirror the REAL attention flag + the name-tag subject onto the WORLD
    // store so the renderer draws the ghost's "!" / name tag from real server state (only when
    // the ghost already exists — upsert never phantoms coarse state).
    if (useWorldStore.getState().ghosts[gid]) {
      useWorldStore.getState().upsertGhost({
        id: gid,
        attention: attention ?? null,
        subject: str(p.task) ?? str(p.name),
      });
    }
  }

  /** Route the STAGE-4 inspector envelopes into the inspector store (frames/status/errors). */
  /** Route a ghost's real scraped record into its inspector Activity feed (keyed by ghost). */
  private applyScrapedRecord(env: Envelope): void {
    const gid = env.ghost_id;
    if (!gid) return;
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const rec = (p.fields ?? p.record) as unknown;
    if (rec && typeof rec === "object") {
      // Surface the url alongside the fields so the activity card is self-describing.
      const withUrl =
        typeof p.url === "string" && !(rec as Record<string, unknown>).url
          ? { url: p.url, ...(rec as Record<string, unknown>) }
          : rec;
      inspectorStore.getState().pushRecords(gid, [withUrl]);
    }
  }

  private applyInspector(env: Envelope): void {
    const gid = env.ghost_id;
    if (!gid) return;
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const store = inspectorStore.getState();
    switch (env.type) {
      case "browser.frame":
        if (typeof p.ref === "string") store.applyFrame(gid, p.ref);
        break;
      case "browser.status":
        store.applyStatus(
          gid,
          typeof p.current_url === "string" ? p.current_url : null,
          typeof p.title === "string" ? p.title : null,
        );
        break;
      case "browser.view": {
        // The server's ghost-type-aware view-mode envelope. `view:"live"` →
        // browser-nav (live frame), `view:"activity"` → api-only (activity/data view). It
        // also carries the SESSION-scoped sanitized persona sentence. We mirror both
        // onto the world-store ghost so the inspector branches on `ghost.workKind` and shows
        // the `ghost.persona` chip. Persona passes the TS surface gate (last line) — a leaked
        // token is dropped to null (omit-not-leak), never rendered.
        const workKind: GhostWorkKind | null =
          p.view === "activity" ? "api-only" : p.view === "live" ? "browser-nav" : null;
        const persona =
          typeof p.persona === "string" && p.persona.trim().length > 0 && isSurfaceSafe(p.persona)
            ? p.persona
            : null;
        useWorldStore.getState().upsertGhost({
          id: gid,
          ...(workKind ? { workKind } : {}),
          persona,
        });
        // The server's HONEST reason the live view can't stream yet (session
        // opening, or the workspace live-view capability off). Guard it here too (last line) so
        // a raw/vendor reason is dropped to null rather than stored; a null clears it.
        const reason =
          typeof p.reason === "string" && p.reason.trim().length > 0 && isSurfaceSafe(p.reason)
            ? p.reason
            : null;
        store.applyView(gid, reason);
        break;
      }
      case "browser.error":
      case "task.retry": {
        // Boundary: render the SERVER-curated `display` phrase; if absent (e.g. a raw
        // relayed error) fall back to a safe generic — NEVER the raw provider/vendor `code`.
        const shown = safeSurfaceText(
          typeof p.display === "string" ? p.display : p.code,
          "Held at the gate…",
        );
        store.pushError(gid, shown, p.retryable === true, env.ts);
        store.pushEvent(gid, `${env.type}: ${shown}`, env.ts);
        break;
      }
      case "browser.data":
        if (Array.isArray(p.records)) store.pushRecords(gid, p.records as unknown[]);
        break;
      default:
        break;
    }
  }

  private applySpawn(env: Envelope): void {
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const id = typeof p.id === "string" ? p.id : env.ghost_id;
    if (!id) return;
    const section = typeof p.section === "string" ? p.section : null;
    useWorldStore.getState().upsertGhost({
      id,
      name: typeof p.name === "string" ? p.name : id,
      home_grave: typeof p.home_grave === "string" ? p.home_grave : "",
      section,
      color: typeof p.color === "number" ? p.color : null,
      state: "IDLE",
      position: asPoint(p.position),
    });
    // seed the roster row up front (name/section/behavior are known at spawn) so the ghost
    // appears in its section group immediately, before the first status tick.
    rosterStore.getState().seed(
      id,
      typeof p.name === "string" ? p.name : id,
      section ?? "",
      typeof p.behavior === "string" ? p.behavior : "",
    );
  }

  /** An operator removed a ghost (per-section '-' control) — drop it everywhere. */
  private applyDespawn(env: Envelope): void {
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const id = typeof p.ghost_id === "string" ? p.ghost_id : env.ghost_id;
    if (!id) return;
    this.walks.delete(id);
    useWorldStore.getState().removeGhost(id);
    rosterStore.getState().remove(id);
  }

  private applyCommand(env: Envelope): void {
    const gid = env.ghost_id;
    if (!gid) return;
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const kind = typeof p.kind === "string" ? p.kind : "";
    const args = (p.args ?? {}) as Record<string, unknown>;
    const store = useWorldStore.getState();

    // mirror the coarse command into the inspector's Activity log (real event stream).
    if (kind) {
      const detail = typeof args.anim === "string" ? `:${args.anim}` : "";
      inspectorStore.getState().pushEvent(gid, `${kind}${detail}`, env.ts);
    }

    switch (kind) {
      case "walk": {
        const rawPath = Array.isArray(args.path) ? (args.path as unknown[]).map(asPoint) : [];
        const path = rawPath.filter((pt): pt is Point => pt !== null);
        const dest = asPoint(args.destination);
        if (path.length === 0 && dest) path.push(dest);
        if (path.length === 0) return;
        store.setGhostPosition(gid, path[0]);
        store.applyStatusChanged({
          ghost_id: gid,
          state: args.mode === "home" ? "RETURNING_HOME" : "WALKING",
        });
        this.walks.set(gid, { path, seg: 0, segT: 0 });
        break;
      }
      case "face": {
        this.walks.delete(gid);
        // Honor the explicit server facing. `target:"rest"` is an idle-arrival
        // resting facing (stays IDLE → true grave-rest Zzz); anything else is the
        // working ghost facing its workstation (AT_WORKSTATION).
        const facing = asFacing(args.facing);
        const target = typeof args.target === "string" ? args.target : "browser";
        const state: GhostState = target === "rest" ? "IDLE" : "AT_WORKSTATION";
        store.applyStatusChanged({ ghost_id: gid, state, facing });
        break;
      }
      case "anim": {
        const state = ANIM_STATE[typeof args.anim === "string" ? args.anim : ""];
        if (state) {
          this.walks.delete(gid);
          store.applyStatusChanged({ ghost_id: gid, state });
        }
        break;
      }
      // "say" / "overlay" → a transient speech/thought bubble above the ghost (the text is
      // server-provided only — never client-authored copy). The renderer fades it after a beat.
      case "say":
      case "overlay": {
        // 196 FIX 2: `set_overlay(kind)` emits `args.overlay=kind` (e.g. "work"); the client
        // previously read only `args.text`/`args.icon`, so the working overlay never showed.
        // Map the overlay kind → its working-bubble glyph (surface-safe, on-brand — the
        // mapping is a closed client-side dictionary, never server-authored copy).
        const text =
          typeof args.text === "string"
            ? args.text
            : typeof args.icon === "string"
              ? args.icon
              : typeof args.overlay === "string"
                ? overlayGlyph(args.overlay)
                : "";
        if (text) store.pushBubble(gid, text, kind === "overlay" ? "overlay" : "say");
        break;
      }
      default:
        break;
    }
  }

  private advanceWalks(dt: number): void {
    if (this.walks.size === 0) return;
    const store = useWorldStore.getState();
    // Pitfall 2: accumulate EVERY moved ghost for this frame and flush in ONE
    // store write, not N — the segment-lerp math below is unchanged, only the flush.
    const moved: Record<string, Point> = {};
    for (const [gid, w] of this.walks) {
      let budget = this.walkSpeed * dt;
      while (budget > 0 && w.seg < w.path.length - 1) {
        const a = w.path[w.seg];
        const b = w.path[w.seg + 1];
        const segLen = Math.hypot(b.x - a.x, b.y - a.y) || 1e-6;
        const remain = segLen * (1 - w.segT);
        if (budget >= remain) {
          budget -= remain;
          w.seg += 1;
          w.segT = 0;
        } else {
          w.segT += budget / segLen;
          budget = 0;
        }
      }
      const seg = Math.min(w.seg, w.path.length - 1);
      const a = w.path[seg];
      const b = w.path[Math.min(seg + 1, w.path.length - 1)];
      moved[gid] = { x: a.x + (b.x - a.x) * w.segT, y: a.y + (b.y - a.y) * w.segT };
      if (w.seg >= w.path.length - 1) this.walks.delete(gid);
    }
    store.setGhostPositions(moved);
  }
}

/** Options for {@link startLiveClient}. */
export interface LiveClientOptions {
  url?: string;
  token?: string;
  /**
   * The backend's real `/token` endpoint. When set, the client re-fetches a fresh token
   * before every (re)connect so a fresh tab authenticates the self-hosted backend and a tab whose
   * baked token predates a restart self-heals instead of reconnecting forever with the stale one.
   */
  tokenUrl?: string;
  /** walk interpolation speed in world px/sec (default 220 — matches sim). */
  walkSpeed?: number;
  /**
   * The server rebroadcast the authoritative world after a validated `map.save` (or on
   * `map.load`/`map.reset`). `map` is the EditableMap wire shape — the app applies it to the
   * live render loop so all clients + running ghosts pick up the new graveyard.
   */
  onWorldSnapshot?: (map: Record<string, unknown>) => void;
  /** The result of a `map.save`/`map.reset` (ok + a reject reason on failure). */
  onMapSaved?: (ok: boolean, reason: string | null) => void;
  /**
   * The result of a `section.save`/`section.remove` (the department editor). `ok`
   * false carries the server's reject reason (SSRF-blocked target, disallowed label, or a
   * malformed payload) — the editor keeps the form values and surfaces the reason.
   */
  onSectionSaved?: (ok: boolean, reason: string | null) => void;
  /**
   * The server-owned set of ADVANCED real-retail departments currently switched
   * on, relayed on every `workforce.advanced` ack. The App keeps the toggle UI in sync with it.
   */
  onAdvancedToggled?: (advanced: string[]) => void;
}

/** A fan-out mission the operator submits: names + urls only, NEVER a key. */
export interface MissionFanout {
  title: string;
  urls: string[];
  /**
   * A search-driven (query/category) seed for the mission — the alternative to a
   * `urls` list. When present the server splits the mission from the SEARCH results (the
   * backend already supports `query` seed missions). NAMES/targets only, never a key.
   */
  query?: string;
  entrySection: string;
  agentMode: "deterministic" | "llm";
}

/** A runtime management command: NAMES only (behavior/section/workstation), never a key. */
export interface ManageCommand {
  command:
    | "assign_behavior"
    | "assign_section"
    | "pause"
    | "resume"
    | "retarget"
    | "cancel"
    | "send_to_workstation"
    | "recall"
    | "reassign";
  ghostId: string;
  behavior?: string;
  section?: string;
  workstation?: string;
}

/** A running live client: submit missions, then `stop()` to tear the socket + rAF loop down. */
export interface LiveClientHandle {
  /** Send `mission.submit {target_name, url}` — the server runs one REAL session for it. */
  submitMission: (targetName: string, url: string) => void;
  /**
   * Send a STAGE-6 fan-out `mission.submit {urls, entry_section, agent_mode}` — the server
   * splits it + fans out across sections/ghosts through the bounded queue on the selected
   * brain. NAMES + urls only; the Anthropic/GhostCrawl keys stay server-side.
   */
  submitMissionFanout: (mission: MissionFanout) => void;
  /** Send `ghost.manage {command, ghost_id, ...}` — a runtime management command (NAMES only). */
  manageGhost: (cmd: ManageCommand) => void;
  /**
   * Send `workforce.start` — the Live-mode workforce: the server spawns 3 ghosts in EACH
   * of the 6 sections (18 total) running REAL behaviors against example.com with a varied
   * action per ghost, demonstrating GhostCrawl + ghostopia. NAMES + urls only, no key.
   */
  runWorkforce: () => void;
  /** Send `workforce.stop` — dematerialize the running workforce (despawn all its ghosts). */
  stopWorkforce: () => void;
  /**
   * Send `workforce.advanced {id, enabled}` — toggle an opt-in ADVANCED real-retail department
   * Enabling it runs that department against real stores with the operator's own
   * key; disabling it stops it. NAMES only, no key.
   */
  enableAdvanced: (id: string, enabled: boolean) => void;
  /** Send `ghost.spawn {section}` — add one ambient ghost into a section (per-section '+'). */
  spawnGhost: (section: string) => void;
  /** Send `ghost.despawn {ghost_id}` — remove one ghost authoritatively (per-section '-'). */
  despawnGhost: (ghostId: string) => void;
  /**
   * Send `ghost.select {ghost_id}` — the server streams ONLY this ghost's real frames
   * `null` deselects (server stops the stream). Drives the STAGE-4 inspector.
   */
  selectGhost: (ghostId: string | null) => void;
  /** Send `critter.pet {critter_id}` — the server acks a `critter.petted` heart/spark flash. */
  petCritter: (critterId: string) => void;
  /**
   * Send `map.save {map}` — the Graveyard Builder submits the edited DRAFT map. The
   * server VALIDATES it (schema + bounds + catalog allowlist + reachability), and only on a
   * valid map swaps the live world + rebroadcasts `world.snapshot`. `map` is the wire shape.
   */
  saveMap: (map: Record<string, unknown>) => void;
  /** Send `map.load` — request the current authoritative world snapshot (editor open). */
  loadMap: () => void;
  /** Send `map.reset` — restore the built-in designed graveyard. */
  resetMap: () => void;
  /**
   * Send `section.save {section}` — the department editor submits an authored/edited
   * department (label/theme + target_url-OR-query + category + extract_schema + map bounds).
   * The server VALIDATES it (schema → SSRF → surface-language) and only on a valid department
   * goes it live + rebroadcasts `catalog.sections`; a reject arrives via `onSectionSaved`.
   * `section` is the wire SectionDef shape — NAMES/targets only, never a key.
   */
  saveSection: (section: Record<string, unknown>) => void;
  /** Send `section.remove {id}` — drop a department by id (authed WS, NAMES only). */
  removeSection: (id: string) => void;
  stop: () => void;
}

/**
 * Open the authed WS in Live mode: apply real server envelopes to the store (drop-in for
 * `simClient`) and expose a mission-submit. The client holds NO key — it sends only a target
 * NAME + URL and renders whatever the server streams back.
 */
export function startLiveClient(options: LiveClientOptions = {}): LiveClientHandle {
  const applier = new StoreApplier(options.walkSpeed ?? 220);
  const ws = new WsClient({
    url: options.url,
    token: options.token,
    tokenProvider: makeTokenProvider(options.tokenUrl),
    onEnvelope: (env) => {
      // Graveyard Builder: the server-authoritative world snapshot + save result are
      // handled here (they drive the live-map swap + the editor status, not the ghost store).
      if (env.type === "world.snapshot") {
        const m = (env.payload as Record<string, unknown>)?.map;
        if (m && typeof m === "object") options.onWorldSnapshot?.(m as Record<string, unknown>);
        return;
      }
      if (env.type === "map.saved") {
        const p = (env.payload ?? {}) as Record<string, unknown>;
        options.onMapSaved?.(p.ok === true, typeof p.reason === "string" ? p.reason : null);
        return;
      }
      if (env.type === "section.saved") {
        const p = (env.payload ?? {}) as Record<string, unknown>;
        options.onSectionSaved?.(p.ok === true, typeof p.reason === "string" ? p.reason : null);
        // the server separately rebroadcasts catalog.sections (applied via its own envelope).
        return;
      }
      if (env.type === "workforce.advanced") {
        const p = (env.payload ?? {}) as Record<string, unknown>;
        const advanced = Array.isArray(p.advanced)
          ? (p.advanced.filter((x) => typeof x === "string") as string[])
          : [];
        options.onAdvancedToggled?.(advanced);
        return;
      }
      applier.apply(env);
    },
    // On connect, ask the server for the capability catalog (behaviors + sections) so the
    // management dropdowns are populated from the SERVER — adding one needs no UI edit.
    onOpen: () => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "catalog.request",
        ts: Date.now() / 1000,
        payload: {},
      });
    },
  });
  applier.start();
  ws.connect();

  return {
    submitMission: (targetName: string, url: string): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "mission.submit",
        ts: Date.now() / 1000,
        payload: { target_name: targetName, url },
      });
    },
    submitMissionFanout: (mission: MissionFanout): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "mission.submit",
        ts: Date.now() / 1000,
        payload: {
          title: mission.title,
          urls: mission.urls,
          // Forward the query seed only when non-empty (a search-driven mission);
          // a url-list mission omits it, unchanged.
          ...(mission.query && mission.query.trim().length > 0
            ? { query: mission.query.trim() }
            : {}),
          entry_section: mission.entrySection,
          agent_mode: mission.agentMode,
        },
      });
    },
    manageGhost: (cmd: ManageCommand): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "ghost.manage",
        ts: Date.now() / 1000,
        payload: {
          command: cmd.command,
          ghost_id: cmd.ghostId,
          ...(cmd.behavior !== undefined ? { behavior: cmd.behavior } : {}),
          ...(cmd.section !== undefined ? { section: cmd.section } : {}),
          ...(cmd.workstation !== undefined ? { workstation: cmd.workstation } : {}),
        },
      });
    },
    runWorkforce: (): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "workforce.start",
        ts: Date.now() / 1000,
        payload: {},
      });
    },
    stopWorkforce: (): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "workforce.stop",
        ts: Date.now() / 1000,
        payload: {},
      });
    },
    enableAdvanced: (id: string, enabled: boolean): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "workforce.advanced",
        ts: Date.now() / 1000,
        payload: { id, enabled },
      });
    },
    spawnGhost: (section: string): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "ghost.spawn",
        ts: Date.now() / 1000,
        payload: { section },
      });
    },
    despawnGhost: (ghostId: string): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "ghost.despawn",
        ts: Date.now() / 1000,
        payload: { ghost_id: ghostId },
      });
    },
    selectGhost: (ghostId: string | null): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "ghost.select",
        ts: Date.now() / 1000,
        payload: { ghost_id: ghostId },
      });
    },
    petCritter: (critterId: string): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "critter.pet",
        ts: Date.now() / 1000,
        payload: { critter_id: critterId },
      });
    },
    saveMap: (map: Record<string, unknown>): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "map.save",
        ts: Date.now() / 1000,
        payload: { map },
      });
    },
    loadMap: (): void => {
      ws.send({ protocol_version: PROTOCOL_VERSION, type: "map.load", ts: Date.now() / 1000, payload: {} });
    },
    resetMap: (): void => {
      ws.send({ protocol_version: PROTOCOL_VERSION, type: "map.reset", ts: Date.now() / 1000, payload: {} });
    },
    saveSection: (section: Record<string, unknown>): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "section.save",
        ts: Date.now() / 1000,
        payload: { section },
      });
    },
    removeSection: (id: string): void => {
      ws.send({
        protocol_version: PROTOCOL_VERSION,
        type: "section.remove",
        ts: Date.now() / 1000,
        payload: { id },
      });
    },
    stop: (): void => {
      applier.stop();
      rosterStore.getState().clear();
      resultsStore.getState().clear();
      catalogStore.getState().clear();
      metricsStore.getState().clear();
      diagnosticsStore.getState().clear();
      ws.close();
    },
  };
}
