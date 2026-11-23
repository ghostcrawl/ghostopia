// ghostopia web — soundboard pure-logic tests .
//
// Covers the gate (per-event cooldown + global rate cap), the volume clamp, the
// event→sound map, and the disabled/gesture behavior of the Soundboard — all
// WITHOUT a real AudioContext (a lightweight stub records what would play).

import { describe, expect, it } from "vitest";

import {
  clampVolume,
  soundForEnvelope,
  SoundGate,
  Soundboard,
  type SoundName,
} from "./soundboard";

describe("clampVolume", () => {
  it("clamps to [0,1] and maps NaN → 0", () => {
    expect(clampVolume(0.5)).toBe(0.5);
    expect(clampVolume(-1)).toBe(0);
    expect(clampVolume(2)).toBe(1);
    expect(clampVolume(Number.NaN)).toBe(0);
  });
});

describe("soundForEnvelope", () => {
  it("maps the meaningful events to their cues", () => {
    expect(soundForEnvelope("task.completed")).toBe("done");
    expect(soundForEnvelope("result.mission_completed")).toBe("mission");
    expect(soundForEnvelope("ghost.spawned")).toBe("spawn");
  });

  it("treats a blocking browser.error (captcha / non-retryable) as attention", () => {
    expect(soundForEnvelope("browser.error", { code: "captcha_required", retryable: false })).toBe(
      "attention",
    );
    expect(soundForEnvelope("browser.error", { code: "timeout", retryable: true })).toBeNull();
  });

  it("fires attention when a status carries attention.needs", () => {
    expect(soundForEnvelope("ghost.status_changed", { attention: { needs: true } })).toBe(
      "attention",
    );
    expect(soundForEnvelope("ghost.status_changed", { attention: { needs: false } })).toBeNull();
  });

  it("returns null for irrelevant events", () => {
    expect(soundForEnvelope("browser.navigate", { url: "x" })).toBeNull();
    expect(soundForEnvelope("ghost.command")).toBeNull();
  });
});

describe("SoundGate", () => {
  it("enforces the global min-gap between ANY two plays", () => {
    const gate = new SoundGate({ perEventCooldownMs: 0, globalMinGapMs: 300 });
    expect(gate.allow(0, "done")).toBe(true);
    expect(gate.allow(100, "mission")).toBe(false); // within the global gap
    expect(gate.allow(350, "mission")).toBe(true);
  });

  it("enforces a per-event cooldown independent of other cues", () => {
    const gate = new SoundGate({ perEventCooldownMs: 1000, globalMinGapMs: 0 });
    expect(gate.allow(0, "done")).toBe(true);
    expect(gate.allow(500, "done")).toBe(false); // same cue, still cooling down
    expect(gate.allow(500, "attention")).toBe(true); // a different cue is free
    expect(gate.allow(1100, "done")).toBe(true); // cooldown elapsed
  });

  it("reset() clears all history", () => {
    const gate = new SoundGate({ perEventCooldownMs: 1000, globalMinGapMs: 1000 });
    expect(gate.allow(0, "done")).toBe(true);
    gate.reset();
    expect(gate.allow(1, "done")).toBe(true);
  });
});

/** A stub AudioContext that records how many oscillators were started. */
function stubCtx(): { ctx: unknown; started: () => number } {
  let started = 0;
  const param = () => ({
    setValueAtTime() {},
    exponentialRampToValueAtTime() {},
  });
  const ctx = {
    currentTime: 0,
    state: "running",
    destination: {},
    resume() {},
    createOscillator() {
      return {
        type: "sine",
        frequency: param(),
        connect() {},
        start() {
          started += 1;
        },
        stop() {},
      };
    },
    createGain() {
      return { gain: param(), connect() {} };
    },
  };
  return { ctx, started: () => started };
}

describe("Soundboard", () => {
  it("does nothing when disabled", () => {
    const { ctx, started } = stubCtx();
    const board = new Soundboard({ ctxFactory: () => ctx as never });
    board.setEnabled(false);
    board.handle("task.completed", {}, "g1");
    expect(started()).toBe(0);
  });

  it("plays a cue + pings the ghost once enabled, honouring the gate", () => {
    const { ctx, started } = stubCtx();
    const pinged: string[] = [];
    const board = new Soundboard({
      ctxFactory: () => ctx as never,
      gate: new SoundGate({ perEventCooldownMs: 10_000, globalMinGapMs: 0 }),
      onPing: (g) => pinged.push(g),
    });
    board.setEnabled(true);
    board.handle("task.completed", {}, "g1");
    board.handle("task.completed", {}, "g1"); // gated out (same cue, cooling down)
    expect(pinged).toEqual(["g1"]);
    expect(started()).toBeGreaterThan(0);
  });

  it("scales note gain by the volume without exceeding 1", () => {
    const board = new Soundboard({ ctxFactory: () => null });
    board.setVolume(3);
    expect(board.volume).toBe(1);
    board.setVolume(-2);
    expect(board.volume).toBe(0);
  });

  it("play() is a no-op without an AudioContext", () => {
    const board = new Soundboard({ ctxFactory: () => null });
    const cues: SoundName[] = ["done", "attention", "mission", "spawn"];
    for (const c of cues) expect(() => board.play(c)).not.toThrow();
  });
});
