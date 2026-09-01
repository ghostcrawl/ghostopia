// ghostopia web — the Simulated-mode WS client (STAGE 2).
//
// The thin renderer's ONLY link to the Python server. It opens the authed
// WebSocket, requests the simulation (`sim.start`), and applies the inbound
// server-authoritative envelopes to the SAME Zustand store the ticker reads:
//
//   • ghost.spawned  -> upsert the ghost (id / name / colour / home / position)
//   • ghost.command  -> the driver's visual commands (walk / face / anim / say /
//                       overlay) become store state the GhostSprite animates:
//       - walk : interpolate the ghost along the A* path the server sent (a
//                real path, no teleport) + set WALKING / RETURNING_HOME
//       - face : arrived at the workstation -> AT_WORKSTATION
//       - anim : work -> EXTRACTING · error -> ERROR · success -> COMPLETED
//
// The server owns which ghosts/behaviours run (FakeBrowserProvider, no SDK); the
// client only interpolates + renders. This file imports NO backend package and NO
// key — it speaks the validated WS envelope contract only.

import { useWorldStore, type Facing, type GhostState, type Point } from "@ghostopia/ghost-renderer";

import { connectionStore } from "./hud/connectionStore";
import { overlayGlyph } from "./overlayGlyph";
import { soundboard } from "./sound/soundboardInstance";
import { applyWorldEnvelope } from "./worldEnvelope";

const PROTOCOL_VERSION = 1;

/** The normalized wire envelope (mirrors the server's Pydantic Envelope). */
interface Envelope {
  protocol_version: number;
  type: string;
  ghost_id?: string | null;
  ts: number;
  payload?: unknown;
}

/** Options for {@link SimClient}. Sensible localhost defaults for the local dev. */
export interface SimClientOptions {
  /** ws:// URL of the authed gateway. Default: `VITE_GHOSTOPIA_WS_URL` or localhost. */
  url?: string;
  /** the operator's HS256 JWT (minted server-side). Default: `?token=` or the env. */
  token?: string;
  /** walk interpolation speed in world px/sec (default 220). */
  walkSpeed?: number;
}

interface WalkAnim {
  path: Point[];
  seg: number;
  segT: number;
}

function envValue(key: string): string | undefined {
  const env = (import.meta as unknown as { env?: Record<string, string> }).env;
  return env?.[key];
}

function defaultToken(): string {
  const fromUrl = new URLSearchParams(window.location.search).get("token");
  return fromUrl ?? envValue("VITE_GHOSTOPIA_WS_TOKEN") ?? "";
}

function defaultUrl(): string {
  return envValue("VITE_GHOSTOPIA_WS_URL") ?? "ws://localhost:8000/ws";
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

/**
 * Connects to the authed WS, drives the store from server envelopes, and
 * interpolates walks on a rAF loop. `connect()` opens it; `disconnect()` tears it
 * down (used by the App's "Simulated" toggle).
 */
export class SimClient {
  private readonly url: string;
  private readonly token: string;
  private readonly walkSpeed: number;
  private ws: WebSocket | null = null;
  private raf = 0;
  private lastTs = 0;
  private readonly walks = new Map<string, WalkAnim>();
  private closed = false;

  constructor(options: SimClientOptions = {}) {
    this.url = options.url ?? defaultUrl();
    this.token = options.token ?? defaultToken();
    this.walkSpeed = options.walkSpeed ?? 220;
  }

  connect(): void {
    this.closed = false;
    connectionStore.getState().set("connecting");
    const sep = this.url.includes("?") ? "&" : "?";
    const full = this.token ? `${this.url}${sep}token=${encodeURIComponent(this.token)}` : this.url;
    const ws = new WebSocket(full);
    this.ws = ws;

    ws.addEventListener("open", () => {
      connectionStore.getState().set("open");
      this.send({ protocol_version: PROTOCOL_VERSION, type: "sim.start", ts: Date.now() / 1000, payload: {} });
    });
    ws.addEventListener("message", (ev) => this.onMessage(ev.data));
    ws.addEventListener("close", () => {
      if (!this.closed) connectionStore.getState().set("reconnecting");
    });

    this.lastTs = performance.now();
    const step = (now: number): void => {
      const dt = Math.min(0.1, (now - this.lastTs) / 1000);
      this.lastTs = now;
      this.advanceWalks(dt);
      if (!this.closed) this.raf = requestAnimationFrame(step);
    };
    this.raf = requestAnimationFrame(step);
  }

  /**
   * Select (or clear) a ghost from a canvas click. Sim mode has no live frame stream, so this
   * is the client-side equivalent of the roster/canvas selection: it focuses the ghost in the
   * shared world store (an equivalent path to Live mode's `ghost.select`).
   */
  selectGhost(id: string | null): void {
    useWorldStore.getState().selectGhost(id);
  }

  disconnect(): void {
    this.closed = true;
    connectionStore.getState().set("disconnected");
    if (this.raf) cancelAnimationFrame(this.raf);
    this.raf = 0;
    this.walks.clear();
    if (this.ws) {
      try {
        if (this.ws.readyState === WebSocket.OPEN) {
          this.send({ protocol_version: PROTOCOL_VERSION, type: "sim.stop", ts: Date.now() / 1000, payload: {} });
        }
        this.ws.close();
      } catch {
        /* already closing */
      }
      this.ws = null;
    }
  }

  private send(env: Envelope): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(env));
  }

  private onMessage(data: unknown): void {
    if (typeof data !== "string") return;
    let env: Envelope;
    try {
      env = JSON.parse(data) as Envelope;
    } catch {
      return; // ignore non-JSON frames
    }
    // Optional sound cues off the sim stream (off unless the operator enabled it).
    soundboard.handle(env.type, (env.payload ?? {}) as Record<string, unknown>, env.ghost_id ?? undefined);
    if (env.type === "ghost.spawned") this.applySpawn(env);
    else if (env.type === "ghost.command") this.applyCommand(env);
    else applyWorldEnvelope(env.type, (env.payload ?? {}) as Record<string, unknown>);
  }

  /** Send a `critter.pet` for a clicked critter; the server acks a heart/spark flash. */
  petCritter(critterId: string): void {
    this.send({
      protocol_version: PROTOCOL_VERSION,
      type: "critter.pet",
      ts: Date.now() / 1000,
      payload: { critter_id: critterId },
    });
  }

  private applySpawn(env: Envelope): void {
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const id = typeof p.id === "string" ? p.id : env.ghost_id;
    if (!id) return;
    useWorldStore.getState().upsertGhost({
      id,
      name: typeof p.name === "string" ? p.name : id,
      home_grave: typeof p.home_grave === "string" ? p.home_grave : "",
      section: typeof p.section === "string" ? p.section : null,
      color: typeof p.color === "number" ? p.color : null,
      state: "IDLE",
      position: asPoint(p.position),
    });
  }

  private applyCommand(env: Envelope): void {
    const gid = env.ghost_id;
    if (!gid) return;
    const p = (env.payload ?? {}) as Record<string, unknown>;
    const kind = typeof p.kind === "string" ? p.kind : "";
    const args = (p.args ?? {}) as Record<string, unknown>;
    const store = useWorldStore.getState();

    switch (kind) {
      case "walk": {
        const rawPath = Array.isArray(args.path) ? (args.path as unknown[]).map(asPoint) : [];
        const path = rawPath.filter((pt): pt is Point => pt !== null);
        const dest = asPoint(args.destination);
        if (path.length === 0 && dest) path.push(dest);
        if (path.length === 0) return;
        store.setGhostPosition(gid, path[0]);
        store.applyStatusChanged({ ghost_id: gid, state: args.mode === "home" ? "RETURNING_HOME" : "WALKING" });
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
      // "say" / "overlay" → a transient speech/thought bubble above the ghost (server-provided
      // text only). The renderer draws + fades it; the sim driver emits the same envelopes.
      case "say":
      case "overlay": {
        // 196 FIX 2: honor `args.overlay` (the `set_overlay(kind)` emit) via the glyph map.
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
