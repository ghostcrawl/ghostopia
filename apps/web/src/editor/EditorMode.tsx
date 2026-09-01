// ghostopia web — the Graveyard Builder chrome (toolbar + palette + actions).
//
// React chrome for the in-app editor: a tool toolbar, the prop palette, a plot-paint section
// picker, undo/redo, save/reset, export/import, and a validity/status line. It reads the
// editor store (draft/tool/selection) and dispatches pure store actions; SAVE/RESET call the
// props (App → liveClient → authed WS `map.save`/`map.reset` — server-validated). Export/import
// are pure client file I/O (download blob / upload+parse). No SDK/key here (thin frontend).

import { useMemo, useRef } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import { editorStore, type EditorTool } from "./editorStore.js";
import { exportMapJson, importMapJson } from "./mapio.js";
import { PropPalette } from "./PropPalette.js";
import { surfaceLabel } from "../hud/surfaceVocab.js";

const TOOLS: Array<{ id: EditorTool; label: string }> = [
  { id: "place", label: "place" },
  { id: "select", label: "select/move" },
  { id: "rotate", label: "rotate" },
  { id: "recolor", label: "recolor" },
  { id: "toggle", label: "toggle" },
  { id: "erase", label: "erase" },
  { id: "eyedropper", label: "eyedropper" },
  { id: "paint-plot", label: "paint plot" },
];

export interface EditorModeProps {
  /** send the current draft to the server (`map.save`) — server validates + swaps live. */
  onSave: () => void;
  /** restore the built-in designed graveyard (`map.reset`). */
  onReset: () => void;
  /** leave the editor. */
  onExit: () => void;
}

/** The Graveyard Builder panel. Rendered only while the editor is active. */
export function EditorMode({ onSave, onReset, onExit }: EditorModeProps): JSX.Element | null {
  const active = useStore(editorStore, (s) => s.active);
  const draft = useStore(editorStore, (s) => s.draft);
  const tool = useStore(editorStore, (s) => s.tool);
  const status = useStore(editorStore, (s) => s.status);
  const saveResult = useStore(editorStore, (s) => s.saveResult);
  const paintSection = useStore(editorStore, (s) => s.paintSection);
  const revision = useStore(editorStore, (s) => s.revision);
  const fileRef = useRef<HTMLInputElement>(null);

  // sections available to paint plots into (from the draft's areas + regions).
  const sections = useMemo(() => {
    if (!draft) return [];
    const set = new Set<string>();
    for (const a of draft.areas) set.add(a.section);
    for (const id of Object.keys(draft.regions)) set.add(id);
    return [...set].sort();
  }, [draft, revision]);

  if (!active || !draft) return null;

  const canUndo = editorStore.getState().canUndo();
  const canRedo = editorStore.getState().canRedo();

  const doExport = (): void => {
    const text = exportMapJson(draft);
    const blob = new Blob([text], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${draft.name || "graveyard"}.map.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>): Promise<void> => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-importing the same file
    if (!file) return;
    try {
      const text = await file.text();
      editorStore.getState().loadDraft(importMapJson(text));
      editorStore.setState({ saveResult: `imported ${file.name}` });
    } catch (err) {
      editorStore.setState({ saveResult: `import failed: ${err instanceof Error ? err.message : String(err)}` });
    }
  };

  return (
    <div className="editor" role="region" aria-label="graveyard builder">
      <div className="editor__head">
        <span className="editor__title">graveyard builder</span>
        <button type="button" className="editor__x" aria-label="close editor" onClick={onExit}>
          ×
        </button>
      </div>

      <div className="editor__tools">
        {TOOLS.map((t) => (
          <button
            type="button"
            key={t.id}
            className={`editor__tool${tool === t.id ? " editor__tool--on" : ""}`}
            aria-pressed={tool === t.id}
            onClick={() => editorStore.getState().setTool(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tool === "paint-plot" && (
        <div className="editor__paint">
          <label className="editor__paint-label" htmlFor="editor-paint-section">
            plot section
          </label>
          <select
            id="editor-paint-section"
            className="editor__select"
            value={paintSection ?? ""}
            onChange={(e) => editorStore.getState().setPaintSection(e.target.value)}
          >
            <option value="">— pick —</option>
            {sections.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </div>
      )}

      <PropPalette />

      <div className="editor__actions">
        <button type="button" className="editor__btn" disabled={!canUndo} onClick={() => editorStore.getState().undo()}>
          undo
        </button>
        <button type="button" className="editor__btn" disabled={!canRedo} onClick={() => editorStore.getState().redo()}>
          redo
        </button>
        <button type="button" className="editor__btn editor__btn--save" onClick={onSave}>
          save
        </button>
        <button type="button" className="editor__btn" onClick={onReset}>
          reset
        </button>
        <button type="button" className="editor__btn" onClick={doExport}>
          export
        </button>
        <button type="button" className="editor__btn" onClick={() => fileRef.current?.click()}>
          import
        </button>
        <input ref={fileRef} type="file" accept="application/json,.json" hidden onChange={onFile} />
      </div>

      <div className="editor__status">{status}</div>
      {saveResult && <div className="editor__result">{saveResult}</div>}
      <div className="editor__hint">
        tap to {tool} · right-click erases · {surfaceLabel("drag to pan")} · pinch/scroll to zoom
      </div>
    </div>
  );
}
