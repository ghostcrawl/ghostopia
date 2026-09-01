// ghostopia web — the Departments panel.
//
// The operator's "easily edit what it is" surface: add / edit / remove a themed scraping
// DEPARTMENT — a label/theme + a what-to-scrape identity (a target_url OR a search query) +
// a category + an extract_schema (a simple field list) + map placement (bounds). Save emits the
// authed-WS `section.save` verb with the assembled SectionDef; Remove emits
// `section.remove {id}`. The server VALIDATES every department (schema → SSRF → surface-language)
// and, on a reject, returns a reason the panel surfaces WITHOUT clearing the form.
//
// Thin-frontend throughout: this file imports NO GhostCrawl SDK and sends NO key — only NAMES /
// targets / labels. The department LIST is server-relayed via `catalogStore`, so the
// server owns the source of truth.

import { useEffect, useState } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import { catalogStore, type CatalogSection } from "./catalogStore";

/** The result of the last `section.save`/`section.remove` — drives the reject/ok status line. */
export interface SectionSaveResult {
  ok: boolean;
  reason: string | null;
}

export interface DepartmentsPanelProps {
  /** Emit `section.save {section}` with the assembled SectionDef (NAMES/targets only). */
  onSave: (section: Record<string, unknown>) => void;
  /** Emit `section.remove {id}` for a department. */
  onRemove: (id: string) => void;
  /** The last server save result — `ok:false` carries the reject reason (form is kept). */
  saveResult?: SectionSaveResult | null;
}

type TargetMode = "url" | "query";

interface FormState {
  editingId: string | null;
  label: string;
  targetMode: TargetMode;
  targetUrl: string;
  query: string;
  category: string;
  schemaText: string;
  role: string;
  capacity: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

const BLANK: FormState = {
  editingId: null,
  label: "",
  targetMode: "url",
  targetUrl: "",
  query: "",
  category: "",
  schemaText: "",
  role: "extraction",
  capacity: 4,
  x: 0,
  y: 0,
  w: 6,
  h: 6,
};

/** A url-safe id derived from a label (edit keeps the original id). */
function slugify(label: string): string {
  return label
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/** Parse a newline field list ("title", "price: number") into an extract_schema map. */
export function parseSchema(text: string): Record<string, string> {
  const schema: Record<string, string> = {};
  for (const line of text.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const [rawName, rawType] = trimmed.split(":");
    const name = rawName.trim();
    if (!name) continue;
    schema[name] = (rawType ?? "").trim() || "string";
  }
  return schema;
}

/**
 * Assemble the wire SectionDef from the form. Only the ACTIVE target (url OR query) is sent;
 * empty optional fields are omitted so the server's strict `extra='forbid'` model is happy.
 */
export function buildSectionDef(form: FormState): Record<string, unknown> {
  const id = form.editingId ?? slugify(form.label) ?? "";
  const schema = parseSchema(form.schemaText);
  const def: Record<string, unknown> = {
    id,
    label: form.label.trim(),
    bounds: { x: form.x, y: form.y, w: form.w, h: form.h },
    role: form.role.trim() || "extraction",
    capacity: form.capacity,
    accepts: [],
    routes_to: [],
  };
  if (form.category.trim()) def.category = form.category.trim();
  if (form.targetMode === "url") {
    if (form.targetUrl.trim()) def.target_url = form.targetUrl.trim();
  } else if (form.query.trim()) {
    def.query = form.query.trim();
  }
  if (Object.keys(schema).length > 0) def.extract_schema = schema;
  return def;
}

/** True when the form has enough to submit: a label + an active target. */
function canSave(form: FormState): boolean {
  const hasTarget = form.targetMode === "url" ? form.targetUrl.trim().length > 0 : form.query.trim().length > 0;
  return form.label.trim().length > 0 && hasTarget;
}

/**
 * The Departments panel. Reads the server-relayed department list from `catalogStore` and offers
 * add / edit / remove. Edit prefills the fields the catalog relays (label / target / category);
 * placement (bounds) + the extract_schema are (re)authored here (the schema body stays
 * server-side, thin-frontend — only its presence is relayed).
 */
export function DepartmentsPanel({ onSave, onRemove, saveResult }: DepartmentsPanelProps): JSX.Element {
  const sections = useStore(catalogStore, (s) => s.sections);
  const [form, setForm] = useState<FormState>(BLANK);

  // On a successful save the server rebroadcasts the catalog — clear the form back to "new".
  // On a reject (`ok:false`) keep the form values so the operator can fix + resubmit.
  useEffect(() => {
    if (saveResult?.ok) setForm(BLANK);
  }, [saveResult]);

  const patch = (p: Partial<FormState>): void => setForm((f) => ({ ...f, ...p }));

  const beginEdit = (sec: CatalogSection): void => {
    setForm({
      ...BLANK,
      editingId: sec.id,
      label: sec.label ?? "",
      targetMode: sec.query ? "query" : "url",
      targetUrl: sec.targetUrl ?? "",
      query: sec.query ?? "",
      category: sec.category ?? "",
      role: sec.role ?? "extraction",
      capacity: sec.capacity ?? 4,
    });
  };

  return (
    <section className="departments" aria-label="departments">
      <header className="departments__head">departments — what each team scrapes</header>

      {/* The "point it at your own site" guide. One
          quiet line reusing .editor__hint (no modal, no wizard, no dismissal persistence). */}
      <p className="editor__hint departments__guide">
        point a department at YOUR product page — paste its URL above and its ghosts will bring back
        real title/price/image data.
      </p>

      <ul className="departments__list">
        {sections.map((sec) => (
          <li className="departments__row" key={sec.id}>
            <div className="departments__row-main">
              <span className="departments__label">{sec.label}</span>
              {sec.category && <span className="departments__cat">{sec.category}</span>}
              <span className="departments__target" title={sec.targetUrl ?? sec.query ?? ""}>
                {sec.query ? `search: ${sec.query}` : sec.targetUrl ? hostOf(sec.targetUrl) : "—"}
              </span>
            </div>
            <div className="departments__row-actions" role="group" aria-label={`edit ${sec.id}`}>
              <button
                type="button"
                className="departments__edit"
                aria-label={`edit ${sec.label}`}
                onClick={() => beginEdit(sec)}
              >
                edit
              </button>
              <button
                type="button"
                className="departments__remove"
                aria-label={`remove ${sec.label}`}
                onClick={() => onRemove(sec.id)}
              >
                remove
              </button>
            </div>
          </li>
        ))}
      </ul>

      <form
        className="departments__form"
        aria-label={form.editingId ? "edit department" : "add department"}
        onSubmit={(e) => {
          e.preventDefault();
          if (!canSave(form)) return;
          onSave(buildSectionDef(form));
        }}
      >
        <div className="departments__form-title">
          {form.editingId ? `editing: ${form.editingId}` : "new department"}
          {form.editingId && (
            <button
              type="button"
              className="departments__new"
              aria-label="start a new department"
              onClick={() => setForm(BLANK)}
            >
              + new
            </button>
          )}
        </div>

        <input
          className="departments__input"
          aria-label="department label"
          value={form.label}
          onChange={(e) => patch({ label: e.target.value })}
          placeholder="Horror Books · Spooky Masks"
        />
        <input
          className="departments__input"
          aria-label="department category"
          value={form.category}
          onChange={(e) => patch({ category: e.target.value })}
          placeholder="category / theme (optional)"
        />

        <div className="departments__row2" role="group" aria-label="target mode">
          <button
            type="button"
            className={`departments__mode${form.targetMode === "url" ? " departments__mode--on" : ""}`}
            aria-pressed={form.targetMode === "url"}
            onClick={() => patch({ targetMode: "url" })}
          >
            target url
          </button>
          <button
            type="button"
            className={`departments__mode${form.targetMode === "query" ? " departments__mode--on" : ""}`}
            aria-pressed={form.targetMode === "query"}
            onClick={() => patch({ targetMode: "query" })}
          >
            search query
          </button>
        </div>

        {form.targetMode === "url" ? (
          <input
            className="departments__input"
            aria-label="department target url"
            value={form.targetUrl}
            onChange={(e) => patch({ targetUrl: e.target.value })}
            placeholder="https://books.toscrape.com/catalogue/category/books/horror_31/"
          />
        ) : (
          <input
            className="departments__input"
            aria-label="department query"
            value={form.query}
            onChange={(e) => patch({ query: e.target.value })}
            placeholder="spooky masks"
          />
        )}

        <textarea
          className="departments__input departments__input--schema"
          aria-label="department schema"
          value={form.schemaText}
          onChange={(e) => patch({ schemaText: e.target.value })}
          placeholder={"one field per line\ntitle\nprice"}
          rows={3}
        />

        <fieldset className="departments__bounds" aria-label="department placement">
          <legend>placement (map tiles)</legend>
          {(["x", "y", "w", "h"] as const).map((k) => (
            <label key={k} className="departments__bound">
              {k}
              <input
                type="number"
                aria-label={`bounds ${k}`}
                value={form[k]}
                onChange={(e) => patch({ [k]: Number(e.target.value) } as Partial<FormState>)}
              />
            </label>
          ))}
        </fieldset>

        {saveResult && !saveResult.ok && (
          <p className="departments__reject" role="alert">
            {saveResult.reason ?? "That department couldn't be saved…"}
          </p>
        )}

        <button type="submit" className="departments__save" disabled={!canSave(form)}>
          {form.editingId ? "save changes" : "add department"}
        </button>
      </form>
    </section>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}
