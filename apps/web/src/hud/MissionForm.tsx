// ghostopia web — the STAGE-6 mission form (thin: NAMES + urls only, no key).
//
// The operator submits a fan-out mission — a title, a target list (one url per line) OR a
// seed query, an entry section, and the agent mode (deterministic runner vs the real LLM
// brain). The server splits it + fans it out across sections/ghosts through the bounded
// WorkQueue; this form sends only MODE/section NAMES + urls (the Anthropic/GhostCrawl keys
// stay server-side). Errors/retries are visualised over the ghosts by the
// renderer (driven by the server's browser.error/task.retry envelopes) — not here.

import { useEffect, useMemo, useState } from "react";
import type { JSX } from "react";
import { useStore } from "zustand";

import { catalogStore, type CatalogSection } from "./catalogStore";
import type { MissionFanout } from "../liveClient";

/**
 * The entry sections a mission can be launched into are the real DEPARTMENTS, read live from the
 * server-relayed catalog (`catalogStore`) so the dropdown never drifts from the deployed sections.
 * A department is `kind:"department"` and accepts ghosts (`accepts` non-empty) — those are
 * the result repositories a mission fans out into. Before the catalog arrives we fall back to the
 * four launch departments so the form is usable on first paint.
 */
const FALLBACK_SECTIONS: Array<{ id: string; label: string }> = [
  { id: "horror-books", label: "horror books" },
  { id: "mystery-books", label: "mystery books" },
  { id: "spooky-masks", label: "spooky masks" },
  { id: "spooky-costumes", label: "spooky costumes" },
];

/** Keep only real, ghost-accepting departments (a mission's valid entry points). */
function entryOptions(sections: CatalogSection[]): Array<{ id: string; label: string }> {
  const real = sections
    .filter((s) => s.kind === "department" || (Array.isArray(s.accepts) && s.accepts.length > 0))
    .map((s) => ({ id: s.id, label: s.label || s.id }));
  return real.length > 0 ? real : FALLBACK_SECTIONS;
}

export interface MissionFormProps {
  /** Submit the fan-out mission over the authed WS (server-side split + fan-out). */
  onSubmit: (mission: MissionFanout) => void;
}

/**
 * The thin mission-submit form. Parses the textarea into a url list, defaults the entry
 * section to extraction, and exposes the deterministic|LLM agent-mode selector.
 */
export function MissionForm({ onSubmit }: MissionFormProps): JSX.Element {
  const sections = useStore(catalogStore, (s) => s.sections);
  const options = useMemo(() => entryOptions(sections), [sections]);

  const [title, setTitle] = useState("");
  const [mode, setMode] = useState<"urls" | "query">("urls");
  const [urlsText, setUrlsText] = useState("");
  const [queryText, setQueryText] = useState("");
  // A mission fans out INTO a department (a result repository). The entry section defaults to the
  // first real department and follows the live catalog — never a removed pipeline stage.
  const [entrySection, setEntrySection] = useState(() => options[0]?.id ?? FALLBACK_SECTIONS[0].id);
  const [agentMode, setAgentMode] = useState<"deterministic" | "llm">("deterministic");

  // When the catalog (re)loads, keep the selection valid: if the chosen section is gone, snap to
  // the first real department so the form can never submit into a section that no longer exists.
  useEffect(() => {
    if (!options.some((o) => o.id === entrySection)) {
      setEntrySection(options[0]?.id ?? FALLBACK_SECTIONS[0].id);
    }
  }, [options, entrySection]);

  const urls = urlsText
    .split(/\r?\n/)
    .map((u) => u.trim())
    .filter((u) => u.length > 0);
  const query = queryText.trim();

  // Submit is enabled when the ACTIVE mode has something to send: a url list, or a query.
  const canSubmit = mode === "urls" ? urls.length > 0 : query.length > 0;

  const switchMode = (next: "urls" | "query"): void => {
    // Both a url-list and a search mission fan out into the same department (the chosen entry
    // section stands) — only the target input changes.
    setMode(next);
  };

  return (
    <form
      className="mission-form mission-form--fanout"
      onSubmit={(e) => {
        e.preventDefault();
        if (!canSubmit) return;
        if (mode === "query") {
          onSubmit({ title: title || "mission", urls: [], query, entrySection, agentMode });
        } else {
          onSubmit({ title: title || "mission", urls, entrySection, agentMode });
        }
      }}
    >
      <div className="mission-form__row mission-form__modes" role="group" aria-label="mission mode">
        <button
          type="button"
          className={`mission-form__mode${mode === "urls" ? " mission-form__mode--on" : ""}`}
          aria-pressed={mode === "urls"}
          onClick={() => switchMode("urls")}
        >
          url list
        </button>
        <button
          type="button"
          className={`mission-form__mode${mode === "query" ? " mission-form__mode--on" : ""}`}
          aria-pressed={mode === "query"}
          onClick={() => switchMode("query")}
        >
          search (query)
        </button>
      </div>
      <input
        className="mission-form__input"
        aria-label="mission title"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="mission title (find SaaS pricing)"
      />
      {mode === "urls" ? (
        <textarea
          className="mission-form__input mission-form__input--urls"
          aria-label="mission urls"
          value={urlsText}
          onChange={(e) => setUrlsText(e.target.value)}
          placeholder={"https://a.example/pricing\nhttps://b.example/pricing\n… (one per line)"}
          rows={4}
        />
      ) : (
        <input
          className="mission-form__input mission-form__input--query"
          aria-label="mission query"
          value={queryText}
          onChange={(e) => setQueryText(e.target.value)}
          placeholder="spooky masks · spooky costumes · a category to search"
        />
      )}
      <div className="mission-form__row">
        <label className="mission-form__field">
          section
          <select
            className="mission-form__select"
            aria-label="entry section"
            value={entrySection}
            onChange={(e) => setEntrySection(e.target.value)}
          >
            {options.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        <label className="mission-form__field">
          brain
          <select
            className="mission-form__select"
            aria-label="agent mode"
            value={agentMode}
            onChange={(e) => setAgentMode(e.target.value as "deterministic" | "llm")}
          >
            <option value="deterministic">deterministic</option>
            <option value="llm">LLM (Claude)</option>
          </select>
        </label>
      </div>
      <button type="submit" className="mission-form__submit" disabled={!canSubmit}>
        {mode === "query"
          ? `summon the workforce (search)`
          : `summon the workforce (${urls.length})`}
      </button>
    </form>
  );
}
