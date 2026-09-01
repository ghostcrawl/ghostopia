// ghostopia web — the app-wide Soundboard singleton.
//
// One shared soundboard the live/sim clients feed envelopes into. Its 🔊 ping is
// wired to the world store so the triggering ghost shows a fading speaker glyph.
// Kept out of soundboard.ts so that module stays pure + unit-testable (no store).

import { useWorldStore } from "@ghostopia/ghost-renderer";

import { Soundboard } from "./soundboard";

export const soundboard = new Soundboard({
  onPing: (ghostId) => useWorldStore.getState().pingSound(ghostId),
});
