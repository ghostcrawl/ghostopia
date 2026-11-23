// ghostopia web — empty/error/loading-state wording + theme .
//
// Every empty/error/loading state string across the HUD must read in the ONE on-brand voice:
// gentle, present-tense, spooky-whimsical, and terminated with an ellipsis "…" — never a bare
// clinical "no X" fragment. This drives the inspector tab-body placeholders (Activity/Data /
// Errors empty, and the API-only Browser-tab activity placeholder) through the real components
// and asserts the normalized copy — the runnable proof for the empty-state normalization.

import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { TabBody } from "./tabs";
import type { InspectorSession } from "./inspectorStore";
import type { Ghost } from "@ghostopia/ghost-renderer";

function emptySession(): InspectorSession {
  return {
    ghostId: "g1",
    currentUrl: null,
    title: null,
    latestFrameRef: null,
    events: [],
    records: [],
    errors: [],
  };
}

function ghost(workKind: Ghost["workKind"]): Ghost {
  return { id: "g1", name: "Test", x: 0, y: 0, state: "IDLE", workKind } as Ghost;
}

/** Strip tags → the visible text of a rendered tab body. */
function text(markup: string): string {
  return markup.replace(/<[^>]*>/g, "").trim();
}

describe("inspector empty-state copy is on-brand ()", () => {
  const cases: Array<{ tab: "Activity" | "Data" | "Errors"; label: string }> = [
    { tab: "Activity", label: "Activity" },
    { tab: "Data", label: "Data" },
    { tab: "Errors", label: "Errors" },
  ];

  for (const { tab, label } of cases) {
    it(`${label} empty state reads in the gentle present-tense voice (ends with …)`, () => {
      const t = text(
        renderToStaticMarkup(
          createElement(TabBody, { tab, ghost: ghost("browser-nav"), session: emptySession() }),
        ),
      );
      expect(t.length).toBeGreaterThan(0);
      expect(t, `bare/clinical empty copy: ${t}`).toMatch(/…$/);
      // no bare lowercase "no ..." fragment and no clinical verb.
      expect(t).not.toMatch(/^no\s/);
      expect(t).not.toMatch(/\b(resolv|validat|fetch|loading|connecting)/i);
    });
  }

  it("API-only Browser tab activity placeholder is on-brand (ends with …)", () => {
    const t = text(
      renderToStaticMarkup(
        createElement(TabBody, { tab: "Browser", ghost: ghost("api-only"), session: emptySession() }),
      ),
    );
    expect(t.length).toBeGreaterThan(0);
    expect(t, `bare/clinical activity placeholder: ${t}`).toMatch(/…$/);
  });
});
