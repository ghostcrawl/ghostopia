// ghostopia web — token-endpoint derivation tests .
//
// The frontend authenticates the self-hosted backend by GETting the real `/token` route the
// server mints (auth.mint_token), NOT the `the earlier token` endpoint that was referenced but never
// served. `tokenUrlFrom` derives that endpoint from the gateway WS URL.

import { describe, expect, it } from "vitest";

import { resolvedWsUrl, tokenUrlFrom } from "./wsClient";

describe("tokenUrlFrom () — real /token endpoint", () => {
  it("derives http(s) /token from a ws(s) /ws gateway URL", () => {
    expect(tokenUrlFrom("ws://localhost:8000/ws")).toBe("http://localhost:8000/token");
    expect(tokenUrlFrom("wss://app.example.com/ws")).toBe("https://app.example.com/token");
  });

  it("strips a query string and trailing slash off the /ws path", () => {
    expect(tokenUrlFrom("ws://host:9000/ws?token=abc")).toBe("http://host:9000/token");
  });

  it("never derives the unserved the earlier token endpoint", () => {
    expect(tokenUrlFrom("ws://localhost:8000/ws")).not.toContain("the earlier token");
  });
});

describe("resolvedWsUrl — same-origin derivation (remote-host/LAN fix)", () => {
  const g = globalThis as unknown as { window?: unknown };
  const restore = () => { delete g.window; };

  it("derives ws://<page-host>/ws from the page origin (not hardcoded localhost)", () => {
    g.window = { location: { protocol: "http:", host: "100.64.120.12:8000" } };
    try {
      expect(resolvedWsUrl()).toBe("ws://100.64.120.12:8000/ws");
    } finally {
      restore();
    }
  });

  it("uses wss:// when the page is https (no mixed-content block)", () => {
    g.window = { location: { protocol: "https:", host: "ghostopia.example.com" } };
    try {
      expect(resolvedWsUrl()).toBe("wss://ghostopia.example.com/ws");
    } finally {
      restore();
    }
  });

  it("falls back to localhost when there is no window (SSR/node)", () => {
    restore();
    expect(resolvedWsUrl()).toBe("ws://localhost:8000/ws");
  });
});

