// ghostopia web — the authenticated WS client (JWT handshake + contract validation).
//
// The ONE transport the thin renderer uses to talk to the Python server. It opens the
// authed WebSocket (the operator's HS256 token on `?token=`), validates every
// inbound frame against the MIRRORED envelope contract (`contract/schema.json` — the same
// Pydantic `Envelope` the server enforces), and hands valid envelopes to a subscriber. A
// frame that fails the contract is dropped, never applied to the store.
//
// It imports NO backend package and NO key — it speaks only the validated WS envelope
// contract. Both the Stage-2 sim path and the Stage-3 live path (`liveClient`) ride it.

import schema from "./contract/schema.json";

import { connectionStore } from "./hud/connectionStore";

export const PROTOCOL_VERSION = 1;

/** The normalized wire envelope (mirrors the server's Pydantic `Envelope`). */
export interface Envelope {
  protocol_version: number;
  type: string;
  ghost_id?: string | null;
  ts: number;
  payload?: unknown;
}

/** The subset of JSON-Schema the mirrored `Envelope` def carries (required + types). */
interface EnvelopeSchema {
  required?: string[];
}

// Pull the required-field list straight from the mirrored contract so validation tracks the
// server's Envelope model (no hand-maintained duplicate).
const ENVELOPE_DEF: EnvelopeSchema =
  ((schema as { $defs?: Record<string, EnvelopeSchema> }).$defs?.Envelope) ?? {
    required: ["protocol_version", "type", "ts"],
  };
const REQUIRED = ENVELOPE_DEF.required ?? ["protocol_version", "type", "ts"];

/**
 * Validate an unknown inbound frame against the mirrored envelope contract. Returns the typed
 * `Envelope` when it satisfies the contract (required keys present, correct primitive types,
 * non-empty `type`) or `null` otherwise — the caller drops a `null` (never applied).
 */
export function validateEnvelope(value: unknown): Envelope | null {
  if (typeof value !== "object" || value === null) return null;
  const o = value as Record<string, unknown>;
  for (const key of REQUIRED) {
    if (!(key in o)) return null;
  }
  if (typeof o.protocol_version !== "number") return null;
  if (typeof o.ts !== "number") return null;
  if (typeof o.type !== "string" || o.type.length === 0) return null;
  if ("ghost_id" in o && o.ghost_id !== null && typeof o.ghost_id !== "string") return null;
  return {
    protocol_version: o.protocol_version,
    type: o.type,
    ghost_id: (o.ghost_id as string | null | undefined) ?? null,
    ts: o.ts,
    payload: o.payload,
  };
}

function envValue(key: string): string | undefined {
  const env = (import.meta as unknown as { env?: Record<string, string> }).env;
  return env?.[key];
}

function defaultToken(): string {
  const fromUrl = new URLSearchParams(window.location.search).get("token");
  return fromUrl ?? envValue("VITE_GHOSTOPIA_WS_TOKEN") ?? "";
}

/**
 * The authed-gateway WS URL. An explicit `VITE_GHOSTOPIA_WS_URL` wins (custom / two-port setups);
 * otherwise derive it from the PAGE ORIGIN — the backend serves the built UI AND the `/ws` gateway
 * on the SAME port (one-port `make run`), so the socket is same-origin as the page. A
 * hardcoded `ws://localhost:8000` broke every non-localhost access (LAN / a remote host / a reverse-proxied
 * tab): the page loaded from e.g. `http://100.64.120.12:8000` but the socket dialed `localhost`
 * on the VIEWER's machine → connect failed → an eternal "reconnecting". Matching `wss:` to a
 * `https:` page also avoids a mixed-content block.
 */
function defaultUrl(): string {
  const fromEnv = envValue("VITE_GHOSTOPIA_WS_URL");
  if (fromEnv) return fromEnv;
  if (typeof window !== "undefined" && window.location?.host) {
    const scheme = window.location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${window.location.host}/ws`;
  }
  return "ws://localhost:8000/ws";
}

/** The resolved authed-gateway WS URL (env or localhost default). */
export function resolvedWsUrl(): string {
  return defaultUrl();
}

/**
 * Derive the real backend token endpoint (`/token`) from a gateway WS URL —
 * `ws(s)://host[:port]/ws` → `http(s)://host[:port]/token`. The server mints an operator token
 * there (auth.mint_token), so a fresh tab authenticates against the self-hosted backend and a tab
 * whose baked token predates a restart re-fetches a fresh one. Replaces the `the earlier token` endpoint
 * the frontend referenced but that was never served.
 */
export function tokenUrlFrom(wsUrl: string): string {
  const httpUrl = wsUrl.replace(/^ws/, "http");
  const base = httpUrl.replace(/\/ws(\?.*)?$/, "");
  return `${base.replace(/\/$/, "")}/token`;
}

/** Options for {@link WsClient}. Sensible localhost defaults for the local dev. */
export interface WsClientOptions {
  /** ws:// URL of the authed gateway. Default: `VITE_GHOSTOPIA_WS_URL` or localhost. */
  url?: string;
  /** the operator's HS256 JWT (minted server-side). Default: `?token=` or the env. */
  token?: string;
  /**
   * An optional async token source, re-invoked BEFORE every (re)connect. A fresh tab
   * against a self-hosted backend has no token, and a tab whose baked token predates a restart
   * reconnects forever with the STALE one showing an empty world. When a provider is supplied,
   * each connect fetches a FRESH token (from the backend's `/token` route), so a fresh tab
   * authenticates and a stale tab self-heals instead of staring at nothing. A failed/blank fetch
   * keeps the last good token (never worse than the static behavior).
   */
  tokenProvider?: () => Promise<string | null>;
  /** called with each CONTRACT-VALID inbound envelope. */
  onEnvelope: (env: Envelope) => void;
  /** called once the socket is open (e.g. to send the first control verb). */
  onOpen?: () => void;
}

/**
 * The authenticated WS transport: opens the JWT-gated socket, validates every inbound frame
 * against the mirrored contract, and forwards valid envelopes to `onEnvelope`.
 */
export class WsClient {
  private readonly url: string;
  private token: string;
  private readonly tokenProvider?: () => Promise<string | null>;
  private readonly onEnvelope: (env: Envelope) => void;
  private readonly onOpen?: () => void;
  private ws: WebSocket | null = null;
  /** true once `close()` was called → an unexpected drop reconnects, an intentional one does not. */
  private intentional = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private backoffMs = 800;
  private static readonly MAX_BACKOFF_MS = 8000;

  constructor(options: WsClientOptions) {
    this.url = options.url ?? defaultUrl();
    this.token = options.token ?? defaultToken();
    this.tokenProvider = options.tokenProvider;
    this.onEnvelope = options.onEnvelope;
    this.onOpen = options.onOpen;
  }

  connect(): void {
    this.intentional = false;
    // "connecting" on a fresh open; a retry loop reports "reconnecting" via the store already.
    if (connectionStore.getState().state !== "reconnecting") {
      connectionStore.getState().set("connecting");
    }
    // 196: refresh the token before opening (stale-tab self-heal) when a provider is wired, then
    // open. The fetch is async but connect() stays fire-and-forget so callers are unchanged.
    void this.openSocket();
  }

  private async openSocket(): Promise<void> {
    if (this.tokenProvider) {
      try {
        const fresh = await this.tokenProvider();
        if (fresh) this.token = fresh;
      } catch {
        /* keep the last good token — never worse than the static behavior */
      }
      if (this.intentional) return; // closed while the token fetch was in flight
    }
    const sep = this.url.includes("?") ? "&" : "?";
    const full = this.token
      ? `${this.url}${sep}token=${encodeURIComponent(this.token)}`
      : this.url;
    let ws: WebSocket;
    try {
      ws = new WebSocket(full);
    } catch {
      this.scheduleReconnect();
      return;
    }
    this.ws = ws;
    ws.addEventListener("open", () => {
      this.backoffMs = 800;
      connectionStore.getState().set("open");
      this.onOpen?.();
    });
    ws.addEventListener("message", (ev) => this.onMessage(ev.data));
    ws.addEventListener("close", () => this.onClose());
    // an "error" is followed by "close"; let close own the reconnect decision.
  }

  private onClose(): void {
    this.ws = null;
    if (this.intentional) {
      connectionStore.getState().set("disconnected");
      return;
    }
    this.scheduleReconnect();
  }

  /** Schedule a capped-backoff reconnect (unless intentionally closed). */
  private scheduleReconnect(): void {
    if (this.intentional) return;
    connectionStore.getState().noteReconnectAttempt();
    if (this.reconnectTimer !== null) return;
    const delay = this.backoffMs;
    this.backoffMs = Math.min(WsClient.MAX_BACKOFF_MS, this.backoffMs * 2);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (!this.intentional) this.connect();
    }, delay);
  }

  /** Send an envelope up to the server (JSON text over the authed socket). */
  send(env: Envelope): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(env));
    }
  }

  close(): void {
    this.intentional = true;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      try {
        this.ws.close();
      } catch {
        /* already closing */
      }
      this.ws = null;
    }
    connectionStore.getState().set("disconnected");
  }

  private onMessage(data: unknown): void {
    if (typeof data !== "string") return;
    let parsed: unknown;
    try {
      parsed = JSON.parse(data);
    } catch {
      return; // ignore non-JSON frames
    }
    const env = validateEnvelope(parsed);
    if (env !== null) this.onEnvelope(env); // a contract-invalid frame is dropped, never applied
  }
}
