// ghostopia ghost-renderer — pure camera pan/zoom math + clamping.
//
// The camera is a world-space centre point `{x, y}` (in world pixels) + a
// `zoom`. It is server-independent render state held in the Zustand store; the
// render loop reads it each frame and applies it as the world container's
// transform (no React re-render). Everything here is PURE — no PixiJS, no DOM —
// so it is unit-testable without a canvas.

/** The camera: a world-space centre point + a zoom factor. */
export interface Camera {
  x: number;
  y: number;
  zoom: number;
}

/** World-space clamp bounds for the camera centre (in world pixels). */
export interface CameraBounds {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** Sane zoom floor/ceiling — a ghost world is readable between these. */
export const MIN_ZOOM = 0.25;
export const MAX_ZOOM = 8;

/** Clamp a zoom factor to the sane [MIN_ZOOM, MAX_ZOOM] range. */
export function clampZoom(zoom: number): number {
  if (!Number.isFinite(zoom)) return 1;
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom));
}

function clampAxis(v: number, lo: number, hi: number): number {
  if (!Number.isFinite(v)) return lo;
  // Tolerate inverted bounds (lo>hi) by pinning to lo.
  if (hi < lo) return lo;
  return Math.min(hi, Math.max(lo, v));
}

/**
 * Clamp a camera to sane zoom bounds and (optionally) world-space centre bounds.
 * With no `bounds`, only the zoom is clamped (x/y are free).
 */
export function clampCamera(cam: Camera, bounds?: CameraBounds): Camera {
  const zoom = clampZoom(cam.zoom);
  if (!bounds) return { x: cam.x, y: cam.y, zoom };
  return {
    x: clampAxis(cam.x, bounds.minX, bounds.maxX),
    y: clampAxis(cam.y, bounds.minY, bounds.maxY),
    zoom,
  };
}

/**
 * Pan the camera by a SCREEN-space delta (e.g. a pointer drag). Screen deltas
 * are divided by `zoom` so a drag moves the world 1:1 under the cursor at any
 * zoom. Returns a clamped camera.
 */
export function panByScreen(
  cam: Camera,
  screenDx: number,
  screenDy: number,
  bounds?: CameraBounds,
): Camera {
  const z = clampZoom(cam.zoom);
  return clampCamera({ x: cam.x - screenDx / z, y: cam.y - screenDy / z, zoom: z }, bounds);
}

/**
 * Multiply the zoom by `factor` (wheel step), clamped. The camera centre is
 * unchanged (centre-anchored zoom) — the +/- buttons + a keyboard zoom.
 */
export function zoomByFactor(cam: Camera, factor: number, bounds?: CameraBounds): Camera {
  return clampCamera({ x: cam.x, y: cam.y, zoom: cam.zoom * factor }, bounds);
}

/**
 * Zoom by `factor` while keeping the WORLD point `(worldX, worldY)` fixed under
 * the cursor / pinch midpoint (pointer-anchored zoom). Derived so the point's
 * screen position is invariant: `C' = P - (P - C)·(z/z')`. At a zoom limit
 * (z' clamps to z) the centre is unchanged. Returns a clamped camera.
 */
export function zoomAtPoint(
  cam: Camera,
  factor: number,
  worldX: number,
  worldY: number,
  bounds?: CameraBounds,
): Camera {
  const z = clampZoom(cam.zoom);
  const z2 = clampZoom(z * factor);
  const k = z / z2; // 1 at a limit → no pan
  return clampCamera(
    { x: worldX - (worldX - cam.x) * k, y: worldY - (worldY - cam.y) * k, zoom: z2 },
    bounds,
  );
}

/**
 * One smooth follow step: lerp the camera centre a fraction `alpha` (0..1) toward
 * a target world point, zoom unchanged. `alpha` is clamped; `alpha = 0` is a no-op,
 * `alpha = 1` snaps. Returns a clamped camera.
 */
export function followStep(
  cam: Camera,
  targetX: number,
  targetY: number,
  alpha: number,
  bounds?: CameraBounds,
): Camera {
  const a = Number.isFinite(alpha) ? Math.min(1, Math.max(0, alpha)) : 0;
  return clampCamera(
    { x: cam.x + (targetX - cam.x) * a, y: cam.y + (targetY - cam.y) * a, zoom: cam.zoom },
    bounds,
  );
}

/**
 * The velocity look-ahead LEAD point for the follow camera: the target position pushed
 * forward along its (smoothed) velocity by `lookaheadMs`, so the camera leads a moving ghost
 * instead of trailing it. Pure; `vx`/`vy` are world px per ms. A zero velocity returns the
 * target unchanged (a stationary ghost is centred, not offset).
 */
export function leadPoint(
  x: number,
  y: number,
  vx: number,
  vy: number,
  lookaheadMs: number,
): { x: number; y: number } {
  const k = Number.isFinite(lookaheadMs) ? lookaheadMs : 0;
  return { x: x + (Number.isFinite(vx) ? vx : 0) * k, y: y + (Number.isFinite(vy) ? vy : 0) * k };
}

/**
 * Derive camera clamp bounds from a world's pixel size. The centre is allowed to
 * roam the full world rect (0..worldW, 0..worldH) so the edges can be brought to
 * the viewport centre.
 */
export function boundsFromWorld(worldWidthPx: number, worldHeightPx: number): CameraBounds {
  return { minX: 0, maxX: worldWidthPx, minY: 0, maxY: worldHeightPx };
}
