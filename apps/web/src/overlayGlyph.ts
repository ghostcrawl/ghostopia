// ghostopia web — overlay-kind → working-bubble glyph (196 FIX 2).
//
// A behavior's `set_overlay(kind)` emits `args.overlay=kind` (e.g. "work"). The clients
// previously read only `args.text`/`args.icon`, so the working overlay never rendered. This
// closed client-side dictionary maps the overlay kind → the short on-brand glyph the bubble
// draws. On-brand by construction (only ghostly workforce copy); unknown kinds fall back to
// the generic `work` glyph. Pure — shared by liveClient + simClient and unit-tested.
//
// This file imports NO GhostCrawl SDK and NO key.

export const OVERLAY_GLYPH: Record<string, string> = {
  work: "· · ·",
};

/** Map an overlay kind → its working-bubble glyph; unknown kinds fall back to `work`. */
export function overlayGlyph(kind: string): string {
  return OVERLAY_GLYPH[kind] ?? OVERLAY_GLYPH.work;
}
