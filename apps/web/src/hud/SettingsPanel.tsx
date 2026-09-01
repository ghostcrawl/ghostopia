// ghostopia web — the Settings HUD panel.
//
// A small operator panel gating optional delight: a 🔊 sound toggle (OFF by default,
// gesture-unlocked, persisted to localStorage) + a volume slider. Sound is never
// required — this is the ONLY place it turns on, and browser autoplay policies are
// honoured (the AudioContext resumes on this click). No SDK, no key, no server call.

import { useState } from "react";
import type { JSX } from "react";

import { soundboard } from "../sound/soundboardInstance";

export function SettingsPanel(): JSX.Element {
  const [enabled, setEnabled] = useState<boolean>(soundboard.enabled);
  const [volume, setVolume] = useState<number>(soundboard.volume);
  const [open, setOpen] = useState<boolean>(false);

  const toggleSound = (): void => {
    const next = !enabled;
    soundboard.setEnabled(next); // resumes the AudioContext on this user gesture when turning on
    setEnabled(next);
    if (next) soundboard.play("spawn"); // a gentle confirmation cue so the operator hears it work
  };

  const changeVolume = (v: number): void => {
    soundboard.setVolume(v);
    setVolume(soundboard.volume);
  };

  return (
    <div className={`settings${open ? " settings--open" : ""}`}>
      <button
        type="button"
        className="settings__gear"
        aria-expanded={open}
        aria-label="settings"
        onClick={() => setOpen((v) => !v)}
      >
        ⚙
      </button>
      {open && (
        <div className="settings__panel" role="group" aria-label="settings">
          <div className="settings__title">settings</div>
          <button
            type="button"
            className={`settings__toggle${enabled ? " settings__toggle--on" : ""}`}
            aria-pressed={enabled}
            onClick={toggleSound}
          >
            {enabled ? "🔊 sound on" : "🔈 sound off"}
          </button>
          {enabled && (
            <label className="settings__vol">
              volume
              <input
                type="range"
                min={0}
                max={1}
                step={0.05}
                value={volume}
                onChange={(e) => changeVolume(Number(e.target.value))}
              />
            </label>
          )}
        </div>
      )}
    </div>
  );
}
