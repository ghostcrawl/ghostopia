// ghostopia web — the +/- zoom controls + a fading zoom-level readout (camera UX parity).
//
// A thin HUD affordance over the PixiJS canvas: the buttons drive the SAME Zustand
// camera the wheel/pinch drive (centre-anchored `zoomCamera`), and the readout fades
// out shortly after the last zoom change. React chrome only — no SDK, no key.

import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import { useWorldStore } from "@ghostopia/ghost-renderer";

/** How long the zoom-level readout stays fully visible after a change (ms). */
const READOUT_HOLD_MS = 1200;

export function ZoomControls(): JSX.Element {
  const zoom = useStore(useWorldStore, (s) => s.camera.zoom);
  const [visible, setVisible] = useState(false);
  const firstRender = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // show the readout on every zoom change, then fade after a hold (skip the initial mount).
  useEffect(() => {
    if (firstRender.current) {
      firstRender.current = false;
      return;
    }
    setVisible(true);
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setVisible(false), READOUT_HOLD_MS);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [zoom]);

  const zoomIn = (): void => useWorldStore.getState().zoomCamera(1.2);
  const zoomOut = (): void => useWorldStore.getState().zoomCamera(1 / 1.2);

  return (
    <div className="zoomctl">
      <button
        type="button"
        className="zoomctl__btn"
        aria-label="zoom in"
        onClick={zoomIn}
      >
        +
      </button>
      <div
        className={`zoomctl__readout${visible ? " zoomctl__readout--on" : ""}`}
        aria-hidden={!visible}
      >
        {zoom.toFixed(1)}×
      </div>
      <button
        type="button"
        className="zoomctl__btn"
        aria-label="zoom out"
        onClick={zoomOut}
      >
        −
      </button>
    </div>
  );
}
