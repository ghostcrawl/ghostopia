// ghostopia web — event-driven WebAudio soundboard.
//
// ORIGINAL synthesized "spooky chimes" — built live from oscillators + gain
// envelopes, NO audio files and NO reference-repo frequencies. A tiny event→sound
// map plays a short cue on meaningful server envelopes (a task finishing, a ghost
// needing the operator, a mission completing, a ghost materializing); a per-event
// cooldown + a global rate cap keep it gentle, and it is OFF until the operator
// opts in via Settings (also gesture-unlocked so browser autoplay policies never
// break it). Sound is optional delight — never required, never spammy.
//
// The pure gate / volume / event-map logic is unit-tested; the AudioContext is
// created lazily so tests (and SSR) never touch a real audio device.

/** The four original cue voices. */
export type SoundName = "done" | "attention" | "mission" | "spawn";

/** localStorage key persisting the operator's sound preference (off by default). */
export const SOUND_PREF_KEY = "ghostopia.sound";

/** Clamp a volume to the inclusive [0,1] range (NaN → 0). */
export function clampVolume(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return v < 0 ? 0 : v > 1 ? 1 : v;
}

/**
 * Map a server envelope to a cue (or `null` for "no sound"). PURE — the whole
 * event→sound policy lives here so it can be unit-tested without any audio device.
 *
 * - `task.completed`            → `done`      (a ghost finished its work)
 * - `result.mission_completed`  → `mission`   (a whole fan-out mission wrapped up)
 * - `ghost.spawned`             → `spawn`     (a ghost materialized)
 * - a blocking `browser.error`  → `attention` (captcha / non-retryable → needs an operator)
 * - a status carrying `attention.needs` → `attention`
 */
export function soundForEnvelope(
  type: string,
  payload: Record<string, unknown> = {},
): SoundName | null {
  switch (type) {
    case "task.completed":
      return "done";
    case "result.mission_completed":
      return "mission";
    case "ghost.spawned":
      return "spawn";
    case "browser.error": {
      const code = typeof payload.code === "string" ? payload.code.toLowerCase() : "";
      const retryable = payload.retryable === true;
      if (!retryable || code.includes("captcha") || code.includes("attention")) return "attention";
      return null;
    }
    case "ghost.status_changed": {
      const attn = payload.attention;
      if (typeof attn === "object" && attn !== null && (attn as { needs?: unknown }).needs === true) {
        return "attention";
      }
      return null;
    }
    default:
      return null;
  }
}

/** Options for {@link SoundGate}. */
export interface SoundGateOptions {
  /** minimum ms between two plays of the SAME cue key. */
  perEventCooldownMs: number;
  /** minimum ms between ANY two plays (a global rate cap so bursts don't machine-gun). */
  globalMinGapMs: number;
}

/** The default gentle gate: ≤ ~3 cues/sec globally, ≥ 700 ms between same-cue repeats. */
export const DEFAULT_GATE: SoundGateOptions = { perEventCooldownMs: 700, globalMinGapMs: 320 };

/**
 * PURE rate limiter for the soundboard: a per-key cooldown AND a global min-gap. `allow(now,key)`
 * returns whether a cue may play now and, when it returns true, records the play (mutating the
 * gate). Unit-tested independently of any AudioContext.
 */
export class SoundGate {
  private lastByKey = new Map<string, number>();
  private lastAny = Number.NEGATIVE_INFINITY;

  constructor(private readonly opts: SoundGateOptions = DEFAULT_GATE) {}

  allow(now: number, key: string): boolean {
    if (now - this.lastAny < this.opts.globalMinGapMs) return false;
    const last = this.lastByKey.get(key) ?? Number.NEGATIVE_INFINITY;
    if (now - last < this.opts.perEventCooldownMs) return false;
    this.lastAny = now;
    this.lastByKey.set(key, now);
    return true;
  }

  /** Reset all history (used when the socket cycles). */
  reset(): void {
    this.lastByKey.clear();
    this.lastAny = Number.NEGATIVE_INFINITY;
  }
}

/** A minimal structural view of the Web Audio surface the synth uses (keeps tests light). */
type AudioLike = Pick<AudioContext, "createOscillator" | "createGain" | "destination" | "currentTime"> & {
  state?: string;
  resume?: () => Promise<void> | void;
};

type CtxFactory = () => AudioLike | null;

/** A single voice note: waveform + start/end frequency (a glide) + timing + peak gain. */
interface Note {
  type: OscillatorType;
  f0: number;
  f1?: number;
  /** start offset (s) from the cue trigger. */
  at: number;
  /** note length (s). */
  dur: number;
  /** peak gain 0..1 before the master volume. */
  gain: number;
}

/**
 * The ORIGINAL cue voicings (frequencies chosen for a soft graveyard idiom — a gentle
 * bell-ish "done", a low hollow "attention", a rising three-note "mission", an airy
 * "spawn" swell). These are our own numbers, not any reference project's chime table.
 */
const VOICES: Record<SoundName, Note[]> = {
  // a two-note minor-third lift — a soft "filed it" chime.
  done: [
    { type: "triangle", f0: 523.25, dur: 0.14, at: 0, gain: 0.5 },
    { type: "triangle", f0: 622.25, dur: 0.22, at: 0.09, gain: 0.42 },
  ],
  // a low hollow gliding tone — reads as "something needs you" without being harsh.
  attention: [
    { type: "sine", f0: 196.0, f1: 146.83, dur: 0.5, at: 0, gain: 0.55 },
    { type: "sine", f0: 98.0, dur: 0.5, at: 0.02, gain: 0.3 },
  ],
  // a rising three-note spectral arpeggio — a small triumphant flourish for a mission.
  mission: [
    { type: "triangle", f0: 440.0, dur: 0.16, at: 0, gain: 0.42 },
    { type: "triangle", f0: 587.33, dur: 0.16, at: 0.12, gain: 0.42 },
    { type: "triangle", f0: 880.0, dur: 0.3, at: 0.24, gain: 0.4 },
  ],
  // an airy upward swell — a ghost condensing into being.
  spawn: [{ type: "sine", f0: 330.0, f1: 494.0, dur: 0.34, at: 0, gain: 0.34 }],
};

/** Options for {@link Soundboard}. */
export interface SoundboardOptions {
  gate?: SoundGate;
  /** injected AudioContext factory (defaults to the real one; tests pass a stub/null). */
  ctxFactory?: CtxFactory;
  /** called with the triggering ghost id so a 🔊 fade can be drawn over it. */
  onPing?: (ghostId: string) => void;
}

function defaultCtxFactory(): AudioLike | null {
  if (typeof window === "undefined") return null;
  const Ctor =
    (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext ??
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return null;
  try {
    return new Ctor();
  } catch {
    return null;
  }
}

/** Read the persisted sound preference (enabled + volume); off by default. */
function readPref(): { enabled: boolean; volume: number } {
  if (typeof localStorage === "undefined") return { enabled: false, volume: 0.6 };
  try {
    const raw = localStorage.getItem(SOUND_PREF_KEY);
    if (!raw) return { enabled: false, volume: 0.6 };
    const o = JSON.parse(raw) as { enabled?: unknown; volume?: unknown };
    return {
      enabled: o.enabled === true,
      volume: typeof o.volume === "number" ? clampVolume(o.volume) : 0.6,
    };
  } catch {
    return { enabled: false, volume: 0.6 };
  }
}

/**
 * The event-driven soundboard. Wire {@link handle} to the live/sim envelope stream; it maps the
 * envelope to a cue, gates it, synthesizes it, and pings the triggering ghost. `enabled` is
 * operator-controlled (Settings), persisted, and OFF by default; the AudioContext is created +
 * resumed lazily on the first {@link unlock} (a user gesture), never at import time.
 */
export class Soundboard {
  private readonly gate: SoundGate;
  private readonly ctxFactory: CtxFactory;
  private readonly onPing?: (ghostId: string) => void;
  private ctx: AudioLike | null = null;
  private _enabled: boolean;
  private _volume: number;

  constructor(options: SoundboardOptions = {}) {
    this.gate = options.gate ?? new SoundGate();
    this.ctxFactory = options.ctxFactory ?? defaultCtxFactory;
    this.onPing = options.onPing;
    const pref = readPref();
    this._enabled = pref.enabled;
    this._volume = pref.volume;
  }

  get enabled(): boolean {
    return this._enabled;
  }
  get volume(): number {
    return this._volume;
  }

  setEnabled(on: boolean): void {
    this._enabled = on;
    if (on) this.unlock();
    this.persist();
  }

  setVolume(v: number): void {
    this._volume = clampVolume(v);
    this.persist();
  }

  private persist(): void {
    if (typeof localStorage === "undefined") return;
    try {
      localStorage.setItem(
        SOUND_PREF_KEY,
        JSON.stringify({ enabled: this._enabled, volume: this._volume }),
      );
    } catch {
      /* storage unavailable — non-fatal */
    }
  }

  /** Lazily create + resume the AudioContext on a user gesture (autoplay-policy safe). */
  unlock(): void {
    if (!this.ctx) this.ctx = this.ctxFactory();
    const ctx = this.ctx;
    if (ctx && ctx.state === "suspended" && typeof ctx.resume === "function") {
      void ctx.resume();
    }
  }

  /** Map + gate + play a cue for one envelope, then ping its ghost. No-op when disabled/gated. */
  handle(type: string, payload: Record<string, unknown> = {}, ghostId?: string | null): void {
    if (!this._enabled) return;
    const cue = soundForEnvelope(type, payload);
    if (!cue) return;
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    if (!this.gate.allow(now, cue)) return;
    this.play(cue);
    if (ghostId && this.onPing) this.onPing(ghostId);
  }

  /** Synthesize a cue from its {@link VOICES} envelope (no-op without an AudioContext). */
  play(cue: SoundName): void {
    if (!this.ctx) this.ctx = this.ctxFactory();
    const ctx = this.ctx;
    if (!ctx) return;
    const t0 = ctx.currentTime;
    for (const n of VOICES[cue]) {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = n.type;
      const start = t0 + n.at;
      const end = start + n.dur;
      osc.frequency.setValueAtTime(n.f0, start);
      if (n.f1 !== undefined && typeof osc.frequency.exponentialRampToValueAtTime === "function") {
        osc.frequency.exponentialRampToValueAtTime(Math.max(1, n.f1), end);
      }
      const peak = clampVolume(n.gain * this._volume);
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(Math.max(0.0002, peak), start + 0.012);
      gain.gain.exponentialRampToValueAtTime(0.0001, end);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(start);
      osc.stop(end + 0.02);
    }
  }

  /** Clear the gate history (call when the socket cycles). */
  reset(): void {
    this.gate.reset();
  }
}
