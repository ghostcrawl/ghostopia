"""``GhostCrawlProvider`` — the concrete ``BrowserProvider`` over the ghostcrawl SDK.

This is the ONLY concrete provider that crosses to the network/SDK. It fulfils
the shared ``BrowserProvider`` Protocol (``ghostopia-browser-provider``) by delegating to
the ``AsyncGhostCrawl`` client resolved from the :class:`TargetRegistry`.

The FULL primitive surface is wired to the REAL Python SDK: session lifecycle,
``nav`` (goto/current_url via ``cdp.url``), ``scrape``, ``live_frames`` over
``recordings.visual(session_id).watch()``, the ``page.*`` primitives (``eval``/``scroll``/
``dom_snapshot``/``wait_for``/``cookies``/``upload``/``download``/``har`` → ``client.page.*``),
``extract``/``search``/``screenshot`` (→ ``client.extract``/``search``/``screenshot``), and
``release``. Every member that the SDK expresses IS an SDK method — no stubs (SDK-first
+ the operator's SDK-parity directive).

The ONE thing the SDK cannot express — a raw HELD mouse drag — rides the CDP-WebSocket
relay minted by ``client.cdp.url(session_id=...)`` through the thin
:class:`~ghostopia_ghostcrawl_provider.cdp_transport.CdpWsTransport` (the ONE documented
non-SDK path). The ergonomic ``mouse``/``keyboard`` helpers drive
the relay on chromium and DEGRADE to ``client.cdp.input`` click/type on FF/WebKit.

Capability scoping: the SDK client is held privately; this provider
exposes NO ``client`` / ``sdk`` / ``ghostcrawl`` / host attribute, so a behavior holding a
``BrowserProvider`` can drive the browser but can never reach the SDK or the network
directly.
"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any
from urllib.parse import urljoin

from ghostcrawl.facade import GhostCrawlError
from ghostopia_browser_provider.provider import (
    BrowserSessionHandle,
    CdpTransport,
    ScrapeResult,
)
from ghostopia_shared.surface_safe import is_surface_safe

from ghostopia_ghostcrawl_provider.cdp_transport import (
    CdpWsTransport,
    WsConnectFn,
    create_cdp_ws_transport,
)
from ghostopia_ghostcrawl_provider.error_map import MappedError, map_sdk_error
from ghostopia_ghostcrawl_provider.input_helpers import InputHelpers, make_input_helpers
from ghostopia_ghostcrawl_provider.keyless_extractor import (
    crawl_policy_filter,
    deterministic_extract,
)
from ghostopia_ghostcrawl_provider.target_registry import TargetRegistry

_DEFAULT_PROFILE = "default"

# The work-kind an inspector fan-out branches on: a nav/render session shows the live
# browser frame; a scrape/extract-only session shows an activity view.
_WORK_KIND_BROWSER_NAV = "browser-nav"
_WORK_KIND_API_ONLY = "api-only"

# The ONLY session-persona fields ghostopia may surface. The SDK's accessor
# already whitelists these; we rebuild the sentence from them and NEVER echo any other key
# (a raw user-agent / engine codename / fingerprint internal can never reach the wire).
_PERSONA_WHITELIST = ("device_class", "os_class", "browser_class", "locale")

# The engine names that can emit a live interactive ``cdp.frame`` (the WORKING live-view path).
# The customer-facing engine name is ``chrome`` (the ``GHOSTOPIA_ENGINE`` default), which the
# SDK/cloud echoes on the session, while ``chromium`` is the equivalent internal name; BOTH name
# the same engine whose ``cdp.frame`` returns real JPEG frames (verified: an 8 KB JPEG on a
# ``chrome`` session). Gating on the literal ``"chromium"`` alone wrongly reported "Live view
# isn't available" for every ``chrome`` session and shut off the frame stream even though frames
# were streamable — this set accepts both names.
_LIVE_VIEW_ENGINES = frozenset({"chromium", "chrome"})

# Customer-safe, HONEST reasons the live browser frame can't stream right now. Each is a
# surface-language-safe sentence (no vendor / engine codename / internal flag name) so the
# inspector shows WHY instead of an eternal "No live view yet…" placeholder. Surfacing the
# real, product-enforced capability gate is honest reporting, NOT a ghostopia-side cover for
# an upstream shortcoming.
_LIVE_VIEW_AWAITING_SESSION = "Waking a browser for this ghost — the live view opens in a moment…"
_LIVE_VIEW_UNSUPPORTED_ENGINE = "Live view isn't available for this session…"
_LIVE_VIEW_CAPABILITY_OFF = (
    "Live view isn't turned on for this workspace yet — enable session recording to watch…"
)


def _persona_sentence(fields: dict[str, Any]) -> str:
    """Build a customer-safe persona sentence from the whitelisted fields ONLY.

    ``"Browsing as a <browser-class> <device> on <os> · <locale>"``; missing fields are
    dropped. Only the whitelist keys are read — an extra ``user_agent``/``engine`` key in
    the payload is ignored, so it can never surface.
    """
    browser = str(fields.get("browser_class") or "").strip()
    device = str(fields.get("device_class") or "").strip()
    os_name = str(fields.get("os_class") or "").strip()
    locale = str(fields.get("locale") or "").strip()
    lead = " ".join(part for part in (browser, device) if part)
    parts: list[str] = []
    if lead:
        parts.append(f"Browsing as a {lead}")
    if os_name:
        parts.append(("on " + os_name) if parts else os_name)
    sentence = " ".join(parts).strip()
    if locale and sentence:
        sentence = f"{sentence} · {locale}"
    elif locale:
        sentence = locale
    return sentence


# The fields a real-retail product card carries. A structured extraction always REQUESTS these
# (unioned with any department-declared field names) so every returned product has its own
# ``{title, price, image, link}`` — the shape the Data Graveyard priced card + best-offer
# comparison consume (R4/R5).
_STRUCTURED_FIELDS: tuple[str, ...] = ("title", "price", "image", "link")


def _schema_field_names(schema: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The field names a department's ``extract_schema`` declares (order-preserving).

    Accepts BOTH shapes ghostopia hands around: a proper JSON-Schema ``{"properties": {...}}``
    object, and the flat ``{field: type}`` map a section carries (e.g. ``{"title": "string"}``).
    Returns an empty tuple for anything else — the caller unions this with the structured
    defaults so a product always carries at least ``title/price/image/link``."""
    if not isinstance(schema, Mapping):
        return ()
    props = schema.get("properties")
    if isinstance(props, Mapping) and props:
        return tuple(str(k) for k in props)
    return tuple(str(k) for k in schema if isinstance(k, str))


def _products_extract_schema(fields: Iterable[str]) -> dict[str, Any]:
    """A JSON Schema (Draft 2020-12) asking for a LIST of product objects under ``products``.

    A retail LISTING page holds MANY products; a flat object schema would coax a single record,
    so the request wraps the per-item fields in a ``products`` array. Kept intentionally lenient
    (no ``required``/``additionalProperties`` on the item) so a page that omits a field for one
    product still validates server-side. Structure only — the same schema for every store (no
    per-site logic)."""
    # A plain string per field. A real listing has products missing a field (no image on a tile,
    # no price on a sold-out item) and the model returns ``null`` for it — GhostCrawl's
    # ``/v1/extract`` is null-tolerant by default (2.3.6-253+): it validates against a null-relaxed
    # copy of the schema, so a plain ``{"type": "string"}`` never 422s on a null field. No
    # explicit ``["string","null"]`` union is needed; the downstream parser drops the blanks.
    item_props = {name: {"type": "string"} for name in fields}
    return {
        "type": "object",
        "properties": {
            "products": {
                "type": "array",
                "items": {"type": "object", "properties": item_props},
            }
        },
        "required": ["products"],
    }


def _records_from_extract_data(url: str, data: Any) -> list[dict[str, Any]]:
    """Lift a ``/v1/extract`` ``data`` payload into ``{title, price, image, link, url}`` records.

    Tolerant of every shape the BYO model may return under the products-array schema: the wrapped
    ``{"products": [...]}`` object (the requested shape), a bare list of product objects, or a
    single product object. Blank fields are dropped; ``link`` defaults to the item's own url then
    the page url and is absolutized, and a relative ``image`` is absolutized too — so each card
    carries its OWN non-blank link/image (R4/R5), never the listing's."""
    items: list[Any]
    if isinstance(data, Mapping):
        nested = next((v for v in data.values() if isinstance(v, list)), None)
        items = nested if nested is not None else [data]
    elif isinstance(data, list):
        items = data
    else:
        return []
    records: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        record = {str(k): v for k, v in item.items() if v not in (None, "")}
        if not record:
            continue
        link = record.get("link") or record.get("url") or url
        record["link"] = urljoin(url, str(link))
        record.setdefault("url", url)
        image = record.get("image")
        if image:
            record["image"] = urljoin(url, str(image))
        records.append(record)
    return records


class ProviderCallError(Exception):
    """A GhostCrawl SDK failure, normalized for the orchestrator.

    Carries the :class:`MappedError` (visual + ``browser.error``/``task.retry`` event
    type + ``retryable``/``retry_after``) so the caller emits the right ghost event and
    honours backoff without re-parsing the raw SDK exception. The original SDK error is
    kept as ``__cause__`` (``raise ... from``).
    """

    def __init__(self, mapped: MappedError) -> None:
        super().__init__(f"{mapped.code or 'error'} (retryable={mapped.retryable})")
        self.mapped = mapped


def _normalize_frame_ref(ref: str, frame_base_url: str | None) -> str:
    """Pass absolute / data URIs through; prefix a relative ref with the target base URL.

    Frame refs are OPAQUE strings (``GHOSTCRAWL_INTEGRATION.md`` §3). One that is already
    ``http(s):`` / ``data:`` renders as-is; an opaque/relative ref is prefixed with the
    target's frame base URL so the client just draws it.
    """
    if ref.startswith(("http://", "https://", "data:")):
        return ref
    if frame_base_url:
        return f"{frame_base_url.rstrip('/')}/{ref.lstrip('/')}"
    return ref


class _Nav:
    """``nav.*`` over ``client.cdp.url`` — the load-bearing navigation surface."""

    def __init__(self, owner: GhostCrawlProvider) -> None:
        self._owner = owner

    async def goto(self, url: str) -> None:
        client = self._owner._require_client()
        self._owner._mark_work("nav")
        # cdp.url MUST carry the bound session_id, else the backend has no session to drive and
        # replies bad_request (this was why session-backed navigation silently failed). The
        # session is always bound before nav runs (OPENING → NAVIGATE in the behaviors).
        await self._owner._guarded(
            client.cdp.url(session_id=self._owner._session_id_or_raise(), url=url, navigate=True)
        )

    async def current_url(self) -> str:
        # NOTE: cdp.url(session_id=...) MINTS A RELAY, it does not report the page URL — the route
        # has no "read current url" mode — so we never pass session_id here (that path would return
        # a wss:// relay, not the page). Not on the workforce hot path; behaviors track their own
        # current url. Kept for the full-primitive surface.
        client = self._owner._require_client()
        res = await self._owner._guarded(client.cdp.url())
        return str(res.get("url", "")) if isinstance(res, dict) else ""


def _to_bytes(res: Any) -> bytes:
    """Coerce a ``page.download`` result to raw ``bytes`` (SDK returns metadata).

    ``client.page.download`` returns a metadata dict; when it carries inline base64
    ``content``/``data`` we decode it to the file bytes, otherwise we return the metadata
    serialized as JSON bytes (never a silent no-op — the caller always gets real bytes).
    """
    if isinstance(res, (bytes, bytearray)):
        return bytes(res)
    if isinstance(res, dict):
        content = res.get("content") or res.get("data")
        if isinstance(content, str):
            try:
                return base64.b64decode(content)
            except (ValueError, TypeError):
                return content.encode()
        return json.dumps(res).encode()
    return str(res).encode()


class _Page:
    """``page.*`` — the in-page primitive surface wired 1:1 to ``client.page.*``."""

    def __init__(self, owner: GhostCrawlProvider) -> None:
        self._owner = owner

    async def eval(self, expr: str) -> Any:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        res = await self._owner._guarded(client.page.eval(expr))
        return res.get("result", res) if isinstance(res, dict) else res

    async def scroll(self, dx: float = 0.0, dy: float = 0.0) -> None:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        await self._owner._guarded(client.page.scroll(dx=dx, dy=dy))

    async def dom_snapshot(self) -> dict[str, Any]:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        res = await self._owner._guarded(client.page.dom_snapshot())
        return res if isinstance(res, dict) else {"snapshot": res}

    async def wait_for(self, condition: str, *, timeout_ms: float = 30_000.0) -> bool:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        res = await self._owner._guarded(
            client.page.wait(condition=condition, timeout_ms=timeout_ms)
        )
        if isinstance(res, dict):
            return bool(res.get("satisfied", res.get("ok", True)))
        return True

    async def cookies(self) -> list[dict[str, Any]]:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        res = await self._owner._guarded(client.page.get_cookies())
        if isinstance(res, dict):
            cookies = res.get("cookies", [])
            return list(cookies) if isinstance(cookies, list) else []
        return list(res) if isinstance(res, list) else []

    async def upload(self, selector: str, path: str) -> None:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        await self._owner._guarded(client.page.upload(selector=selector, path=path))

    async def download(self, url: str) -> bytes:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        res = await self._owner._guarded(client.page.download(url=url))
        return _to_bytes(res)

    async def har(self) -> dict[str, Any]:
        client = self._owner._require_client()
        self._owner._mark_work("render")
        res = await self._owner._guarded(client.page.har())
        return res if isinstance(res, dict) else {"log": res}


class GhostCrawlProvider:
    """The concrete ``BrowserProvider`` over the ghostcrawl Python SDK.

    Bound to ONE ghost's session on ONE registry target. Every member the SDK can express
    delegates to the resolved ``AsyncGhostCrawl`` client; the raw HELD mouse drag rides the
    CDP-WebSocket relay (the ONE non-SDK path) via :class:`CdpWsTransport`.
    """

    def __init__(
        self,
        registry: TargetRegistry,
        *,
        target: str = "cloud",
        engine: str = "auto",
        frame_base_url: str | None = None,
        ws_connect: WsConnectFn | None = None,
        byo_extract_key: str | None = None,
        byo_model_provider: Mapping[str, Any] | None = None,
    ) -> None:
        # The SDK client is held PRIVATELY — never exposed as ``client``/``sdk`` so a
        # capability-scoped holder cannot reach the network/SDK.
        self._registry = registry
        self._target = target
        self._engine = engine
        self._frame_base_url = frame_base_url
        # The BYO structured-extraction key: the user's (or the operator's OWN, for
        # dev/test) LLM/MCP/AI key. When set, a scrape forwards it so the SERVER runs the
        # user's own extraction and returns ``extracted`` for arbitrary/irregular targets.
        # When UNSET (the shipped keyless default), scrapes stay keyless and the deterministic
        # extractor lifts the rendered content — NO operator-key inference (harness-not-AI).
        self._byo_extract_key = byo_extract_key
        # The BYO structured-extraction MODEL: the operator's own connected LLM as a
        # ``{type, api_key, model, base_url?}`` dict. When set, ``extract_products`` runs
        # GhostCrawl's ``/v1/extract`` BYO-model path — the fleet renders + solves the page's
        # challenge, then the operator's OWN model lifts the FULL priced product list from the
        # listing. When UNSET, ``extract_products`` degrades to the in-session rendered read
        # (content-only, no structured prices) — no operator-model inference (harness-not-AI).
        self._byo_model_provider = dict(byo_model_provider) if byo_model_provider else None
        # Injectable ws-connect for the CDP-WS relay (default: real websockets.connect;
        # tests inject a fake socket so no network is touched).
        self._ws_connect = ws_connect
        self._session: BrowserSessionHandle | None = None
        # Which classes of primitive this ghost has exercised — drives ``work_kind()``
        # ("nav"/"render" → browser-nav; "api" → api-only). Reset per session on open().
        self._work_signals: set[str] = set()
        self._nav = _Nav(self)
        self._page = _Page(self)
        # Built on open() once the session engine is known; the transport dials lazily.
        self._transport: CdpWsTransport | None = None
        self._input: InputHelpers | None = None

    # --- internals --------------------------------------------------------------------
    def _require_client(self) -> Any:
        return self._registry.client_for(self._target)

    def _mark_work(self, signal: str) -> None:
        """Record that the ghost exercised a primitive class (``nav``/``render``/``api``)."""
        self._work_signals.add(signal)

    # --- ghost-type + identity accessors -----------------------------------
    def work_kind(self, _handle: Any = None) -> str:
        """The ghost's work-kind for the inspector — ``"browser-nav"`` vs ``"api-only"``.

        A session that has navigated or rendered (``nav.goto`` / ``page.*`` / live frames)
        is ``browser-nav`` and shows the live browser frame; a scrape/extract-only session
        is ``api-only`` and shows an activity view. A fresh live session with no work
        yet DEFAULTS to ``browser-nav`` (the graceful "no live view yet" placeholder), never
        to an api-only activity view. The optional positional is an inspector convenience.
        """
        if self._work_signals & {"nav", "render"}:
            return _WORK_KIND_BROWSER_NAV
        if "api" in self._work_signals:
            return _WORK_KIND_API_ONLY
        return _WORK_KIND_BROWSER_NAV

    async def persona(self, _handle: Any = None) -> str | None:
        """The session's customer-safe persona sentence over the SDK, or ``None``.

        Reads the SDK accessor ``client.session_persona(session_id)`` (an
        ``AsyncGhostCrawl`` member), rebuilds a sentence from the whitelist ONLY
        (device / OS / browser-class / locale), and runs it through the surface-language
        backstop. A value carrying a banned/internal token is OMITTED (returns ``None``)
        rather than leaked as a fallback (Pitfall 3). No session / no accessor / a not-found
        session → ``None``.
        """
        if self._session is None:
            return None
        client = self._require_client()
        accessor = getattr(client, "session_persona", None)
        if accessor is None:
            return None
        try:
            raw = await self._guarded(accessor(self._session.session_id))
        except ProviderCallError:
            return None
        if not isinstance(raw, dict):
            return None
        fields = {key: raw.get(key) for key in _PERSONA_WHITELIST}
        sentence = _persona_sentence(fields)
        if not sentence:
            return None
        # Surface-language backstop: omit rather than leak if any banned token slipped in.
        return sentence if is_surface_safe(sentence) else None

    def _require_session(self) -> BrowserSessionHandle:
        if self._session is None:
            raise RuntimeError("no bound session — call open()/create_session() first")
        return self._session

    async def _guarded(self, awaitable: Any) -> Any:
        """Await an SDK call, routing any ``GhostCrawlError`` through ``map_sdk_error``."""
        try:
            return await awaitable
        except GhostCrawlError as err:
            raise ProviderCallError(map_sdk_error(err)) from err

    async def _mint_relay(self) -> dict[str, Any]:
        """Mint the signed CDP-WS relay for the bound session via ``client.cdp.url``.

        Returns ``{url, expires_in_seconds, engine}`` — the ONE non-SDK path's ONLY SDK
        touch-point (the relay URL itself IS an SDK method; only the persistent WS driver
        that consumes it is non-SDK).
        """
        client = self._require_client()
        session = self._require_session()
        res = await self._guarded(client.cdp.url(session_id=session.session_id))
        if not isinstance(res, dict):
            raise ProviderCallError(map_sdk_error(GhostCrawlError("cdp.url returned no relay")))
        return {
            "url": res.get("url", ""),
            "expires_in_seconds": res.get("expires_in_seconds", 120),
            "engine": res.get("engine", self._engine),
        }

    async def _cdp_input(self, step_type: str, **params: Any) -> Any:
        """The degrade path: ``client.cdp.input`` (navigate/click/type) for FF/WebKit."""
        client = self._require_client()
        session = self._require_session()
        return await self._guarded(
            client.cdp.input(session_id=session.session_id, step_type=step_type, **params)
        )

    def _rebuild_input(self) -> None:
        """(Re)build the mouse/keyboard helpers + relay transport for the bound session.

        chromium → a live :class:`CdpWsTransport` for the raw HELD stroke; other engines →
        no transport (the helpers degrade to ``cdp.input`` click/type).
        """
        if self._session is None:
            self._transport = None
            self._input = None
            return
        transport: CdpWsTransport | None = None
        if self._session.engine == "chromium":
            transport = create_cdp_ws_transport(self._mint_relay, ws_connect=self._ws_connect)
        self._transport = transport
        self._input = make_input_helpers(
            self._session, transport=transport, cdp_input=self._cdp_input
        )

    # --- namespaced primitive surfaces ------------------------------------------------
    @property
    def nav(self) -> _Nav:
        return self._nav

    @property
    def mouse(self) -> Any:
        if self._input is None:
            raise RuntimeError("no bound session — call open()/create_session() first")
        return self._input.mouse

    @property
    def keyboard(self) -> Any:
        if self._input is None:
            raise RuntimeError("no bound session — call open()/create_session() first")
        return self._input.keyboard

    @property
    def page(self) -> _Page:
        return self._page

    def _session_id_or_raise(self) -> str:
        """The bound session id for a session-scoped CDP call, or a clear error if unbound."""
        if self._session is None:
            raise RuntimeError("no live session bound — create_session() must run before nav/cdp")
        return self._session.session_id

    # --- session lifecycle ------------------------------------------------------------
    @property
    def session(self) -> BrowserSessionHandle | None:
        return self._session

    async def create_session(
        self, target: str, profile_name: str | None = None
    ) -> BrowserSessionHandle:
        client = self._require_client()
        # ENGINE-BASED (ad-hoc) session. ``profile_name`` here is the ghost id — a per-ghost
        # label, NOT a server-side saved profile — so sending it as ``profile`` 404s ("no profile
        # named ..."); a fresh org (cloud or self-host) owns zero named profiles anyway. This was
        # why live sessions never opened (every create 404'd) and the inspector showed nothing.
        # ghostopia's per-ghost identity is delivered via mask_config on the managed fleet, not via
        # named profiles, so we open an ad-hoc session on the target engine — the zero-setup path
        # that works with just an API key.
        res = await self._guarded(client.sessions.create(engine=self._engine))
        session_id = str(res["session_id"]) if isinstance(res, dict) else str(res)
        engine = (
            str(res.get("engine", self._engine)) if isinstance(res, dict) else self._engine
        )
        self._session = BrowserSessionHandle(
            session_id=session_id, target=target, engine=engine
        )
        # fresh session → fresh work-kind signal (defaults to browser-nav until work runs).
        self._work_signals = set()
        self._rebuild_input()
        return self._session

    async def open(self, target: str, profile: str | None = None) -> BrowserSessionHandle:
        return await self.create_session(target, profile_name=profile)

    def adopt_session(
        self, session_id: str, engine: str, target: str = "cloud"
    ) -> BrowserSessionHandle:
        """Bind an ALREADY-CREATED (pre-warmed) session to this provider — no SDK create.

        A warm-session pool opens chrome sessions ahead of time (so a featured ghost's browser
        is ready the instant it needs one, not cold-opened); this hands such a session to this
        provider's ``session``/``nav``/``live_frames`` surface exactly as ``create_session``
        would, but WITHOUT another ``sessions.create`` round-trip. The live-session budget is
        already accounted for by the pool that created the session (it holds the slot and
        transfers it to the acquirer), so this never opens a new session. Same client, same
        account → the adopted ``session_id`` drives on this provider's client unchanged."""
        self._session = BrowserSessionHandle(
            session_id=str(session_id), target=target, engine=str(engine)
        )
        # fresh session → fresh work-kind signal (defaults to browser-nav until work runs).
        self._work_signals = set()
        self._rebuild_input()
        return self._session

    async def release(self) -> None:
        if self._session is None:
            return
        client = self._require_client()
        session_id = self._session.session_id
        transport = self._transport
        self._session = None
        self._rebuild_input()
        if transport is not None:
            await transport.close()
        # TERMINATE, not release: ``/release`` only UNPINS a sticky session (it leaves the lease
        # + the per-tenant ``live:sessions`` slot HELD until the reaper TTL-reclaims it), whereas
        # ``/terminate`` deletes the lease AND frees the live-session slot immediately. A ghost is
        # DONE with its session (ghostopia never reuses a session across cycles — each loop opens a
        # fresh one), so unpinning would leak the tenant's tiny concurrent-live-session budget
        # (e.g. growth = 2): every opened session would hold a slot for its whole TTL, the budget
        # would saturate within a couple of ghosts, and ``sessions.create`` would 429-storm the
        # rest into sessionless degrade. Terminating frees the slot the moment work finishes, so
        # the budget stays clean and the live browser inspector keeps working.
        await self._guarded(client.sessions.terminate(session_id))

    # --- top-level work verbs ---------------------------------------------------------
    async def scrape(
        self,
        handle: BrowserSessionHandle,
        url: str,
        extract_schema: dict[str, Any] | None = None,
    ) -> ScrapeResult:
        client = self._require_client()
        self._mark_work("api")
        # BYO structured extraction: forward the user's own extraction key so the SERVER
        # runs their LLM/MCP/AI and returns ``extracted``. Absent → a keyless scrape (the
        # deterministic extractor lifts the rendered content). No operator-key inference.
        extra: dict[str, Any] = {}
        if self._byo_extract_key:
            extra["provider_key"] = self._byo_extract_key
        res = await self._guarded(
            client.scrape(url=url, extract_schema=extract_schema, **extra)
        )
        return self._to_scrape_result(url, res)

    async def scrape_rendered(
        self, handle: BrowserSessionHandle | None, url: str
    ) -> ScrapeResult:
        """Keyless server scrape of ``url`` — the content-only DEGRADE for a no-model department.

        This is the fallback :meth:`extract_products` drops to when NO BYO model is connected.
        It runs a keyless ``client.scrape(url)`` and lifts the rendered content with the
        deterministic keyless extractor + link discovery. It does NOT read the ghost's open live
        session: GhostCrawl's ``/v1/scrape`` has no session-scoped fetch mode (a ``session_id``
        param is silently ignored server-side), so there is no way to scrape "through" the
        already-solved page — passing one was a dead no-op and has been removed. A real protected
        store therefore CAPTCHAs this keyless fetch; the reliable real-retail path is
        :meth:`extract_products` with a connected model, which renders + solves the page through
        the managed fleet itself. (A genuine session-scoped scrape is a GhostCrawl backlog item —
        see the plugin-audit report.)
        """
        client = self._require_client()
        self._mark_work("render")
        extra: dict[str, Any] = {}
        if self._byo_extract_key:
            extra["provider_key"] = self._byo_extract_key
        res = await self._guarded(client.scrape(url=url, **extra))
        return self._to_scrape_result(url, res)

    async def extract_products(
        self,
        handle: BrowserSessionHandle | None,
        url: str,
        schema: Mapping[str, Any] | None = None,
    ) -> ScrapeResult:
        """Lift the FULL priced product LIST from a real-retail LISTING via GhostCrawl ``/v1/extract``.

        This is the reliable real-retail path. A keyless scrape of a protected store's
        listing returns a summarized page with no product grid, and following its category links
        only finds MORE category pages — never priced leaves. GhostCrawl's ``/v1/extract`` instead
        renders the listing through the managed fleet (solving the page's challenge) and returns a
        LIST of ``{title, price, image, link}`` products — one call, every product on the page, with
        prices. Non-per-site: the same products-array schema for every store.

        NO THIRD PARTY BY DEFAULT: GhostCrawl is the all-in-one. The default call sends NO
        ``model_provider``, so GhostCrawl runs its OWN native, deterministic structured-data
        extractor (schema.org JSON-LD / microdata / Open Graph) — the whole priced grid, zero LLM,
        zero per-site selectors, zero third party. A BYO model is a purely OPTIONAL enhancement for
        pages that carry no machine-readable product data: when the operator has connected one
        (``GHOSTOPIA_BYO_EXTRACT_*``), it is passed through so GhostCrawl runs the caller's model
        for semantic extraction. Either way the reliable managed-fleet render is GhostCrawl's.

        Degrades to the in-session rendered read (:meth:`scrape_rendered`) when the extract call
        errors or yields nothing structured — so an advanced department still delivers real content
        rather than a dead ghost."""
        client = self._require_client()
        self._mark_work("api")
        fields = tuple(
            dict.fromkeys([*_schema_field_names(schema), *_STRUCTURED_FIELDS])
        )
        # Default = GhostCrawl-native deterministic extraction (no model_provider). A connected BYO
        # model is an optional enhancement, never a requirement — GhostCrawl is the AIO.
        extract_kwargs: dict[str, Any] = {}
        if self._byo_model_provider:
            extract_kwargs["model_provider"] = self._byo_model_provider
        try:
            res = await self._guarded(
                client.extract(
                    url=url,
                    schema=_products_extract_schema(fields),
                    **extract_kwargs,
                )
            )
        except ProviderCallError:
            # extract fetch error → keep delivering via the rendered session read.
            return await self.scrape_rendered(handle, url)
        # The ``/v1/extract`` envelope carries the structured result under ``data`` (older/mock
        # shapes used ``extracted``) — read either.
        data = None
        if isinstance(res, Mapping):
            data = res.get("data")
            if data is None:
                data = res.get("extracted")
        records = _records_from_extract_data(url, data)
        if not records:
            return await self.scrape_rendered(handle, url)
        return ScrapeResult(records=records)

    @staticmethod
    def _to_scrape_result(url: str, res: Any) -> ScrapeResult:
        # The SDK returns a ``ScrapeResult`` — a ``collections.abc.Mapping`` (dict-accessible
        # via ``.get`` but NOT a ``dict`` SUBCLASS), so an ``isinstance(res, dict)`` guard here
        # rejects EVERY real response and stringifies the whole object into a ``content`` blob
        # ("ScrapeResult({...})"), which is what left the Data Graveyard full of raw
        # ``str(ScrapeResult)`` rows instead of the real extracted/content records. Accept any
        # Mapping so the ``extracted`` / ``content`` keys below are read straight from it.
        if not isinstance(res, Mapping):
            return ScrapeResult(records=[{"url": url, "content": str(res)}])
        extracted = res.get("extracted")
        if extracted is not None:
            # BYO path: the server ran the user's LLM/MCP/AI and returned structured fields.
            records: list[dict[str, Any]] = (
                [dict(extracted)] if isinstance(extracted, Mapping) else [dict(r) for r in extracted]
            )
            # ``link`` is always the page URL; the BYO extraction may omit it, so backfill.
            for record in records:
                record.setdefault("link", record.get("url") or url)
        else:
            # No server-side structured extraction (keyless — the shipped default). Lift the real
            # rendered content into a ``{title, price, image, link}`` record with the deterministic,
            # per-site-FREE keyless extractor so the Data Graveyard shows a real priced card instead
            # of one opaque blob. A card with no found fields keeps its raw content.
            content = res.get("content") or res.get("markdown") or ""
            records = [deterministic_extract(res.get("url", url), content)]
        # GhostCrawl surfaces the page's own links on the scrape envelope (``discovered_urls``,
        # 2.3.6-253+); ghostopia applies only its crawl POLICY (same-host detail pages, skip nav
        # chrome) to them — GhostCrawl owns extracting the links, ghostopia owns which its ghosts
        # walk. Empty/absent for a leaf page with no onward links.
        raw_links = res.get("discovered_urls") or res.get("links") or []
        discovered = crawl_policy_filter(raw_links, res.get("url", url))
        return ScrapeResult(records=records, discovered_urls=discovered)

    async def live_view_status(
        self, handle: BrowserSessionHandle | None = None
    ) -> dict[str, Any]:
        """Probe whether the live browser frame can stream — HONEST, never swallowed (R7).

        Returns ``{"available": bool, "reason": str | None}``. When frames can't stream, the
        ``reason`` is a customer-safe sentence explaining WHY (the session hasn't opened yet, the
        session engine can't emit a frame, or the workspace's live-view capability — the org
        ``cdp_passthrough_enabled`` / ``recording_enabled`` gate — is off) instead of the
        empty/errored ``cdp.frame`` poll being swallowed into an eternal "No live view yet"
        placeholder (see provider ``_live_frames``). Available → ``reason`` is ``None``.

        This is honest reporting of a real, product-enforced capability gate, NOT a
        ghostopia-side cover for an upstream shortcoming: if the live view genuinely can't
        stream, we say so plainly rather than fabricate frames.
        """
        session = handle or self._session
        if session is None:
            return {"available": False, "reason": _LIVE_VIEW_AWAITING_SESSION}
        if session.engine not in _LIVE_VIEW_ENGINES:
            return {"available": False, "reason": _LIVE_VIEW_UNSUPPORTED_ENGINE}
        client = self._require_client()
        try:
            res = await self._guarded(client.cdp.frame(session_id=session.session_id))
        except ProviderCallError:
            # A raised poll = the live-view capability is not enabled for this workspace (the
            # org cdp/recording flags) — surface it, do not swallow.
            return {"available": False, "reason": _LIVE_VIEW_CAPABILITY_OFF}
        if isinstance(res, Mapping):
            data = res.get("data")
            if isinstance(data, str) and data:
                return {"available": True, "reason": None}
        # chromium session, poll succeeded but returned no frame data → capability off.
        return {"available": False, "reason": _LIVE_VIEW_CAPABILITY_OFF}

    def live_frames(
        self, handle: BrowserSessionHandle, signal: Any
    ) -> AsyncIterator[str]:
        return self._live_frames(handle, signal)

    async def _live_frames(
        self, handle: BrowserSessionHandle, signal: Any
    ) -> AsyncIterator[str]:
        # Live view = poll ``cdp.frame`` (a base64 JPEG of the live interactive session) a few fps
        # and yield each as a ``data:`` URI the thin client renders straight into an <img>. This is
        # the WORKING frame path (interactive frame-poll on a chromium session); the R2-backed
        # ``recordings.visual().watch()`` path is NOT used — it produced no frames in practice
        # (empty poll → the inspector hung on "No live view yet"). A transient capture hiccup is
        # swallowed with a brief backoff so one bad poll never kills the whole stream (the
        # frame-fanout loop has no try/except of its own).
        client = self._require_client()
        self._mark_work("render")
        interval = 0.5
        while signal is None or not signal.is_set():
            try:
                res = await self._guarded(client.cdp.frame(session_id=handle.session_id))
            except Exception:  # noqa: BLE001 — transient capture hiccup; keep streaming
                await asyncio.sleep(interval)
                continue
            if isinstance(res, Mapping):
                data = res.get("data")
                fmt = str(res.get("format") or "jpeg")
                if isinstance(data, str) and data:
                    yield f"data:image/{fmt};base64,{data}"
            await asyncio.sleep(interval)

    # --- top-level work verbs wired to the SDK (SDK-first) -----------------------
    async def extract(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Extract structured data from the CURRENT page via ``client.extract``.

        The Protocol's ``extract(schema)`` is session-bound; the SDK's ``extract(url,
        schema)`` is URL-based, so we read the live URL (``cdp.url``) then delegate.
        """
        client = self._require_client()
        self._mark_work("api")
        url = await self._nav.current_url()
        res = await self._guarded(client.extract(url=url, schema=schema))
        if isinstance(res, dict):
            # The real ``/v1/extract`` envelope carries the result under ``data`` (deterministic
            # native AND BYO paths); older/mock shapes used ``extracted``. Read either, else the
            # raw envelope.
            for key in ("data", "extracted"):
                inner = res.get(key)
                if isinstance(inner, dict):
                    return inner
            return res
        return {}

    async def search(self, opts: dict[str, Any]) -> list[dict[str, Any]]:
        """Web search via ``client.search`` — ``opts`` carries ``query``/``engine``/``limit``."""
        client = self._require_client()
        self._mark_work("api")
        query = str(opts.get("query", ""))
        kwargs = {k: opts[k] for k in ("engine", "limit", "provider_key") if k in opts}
        res = await self._guarded(client.search(query, **kwargs))
        if hasattr(res, "get"):  # SearchResult is dict-accessible; plain dict in tests
            results = res.get("results", [])
        else:
            results = getattr(res, "results", [])
        return list(results) if results else []

    async def screenshot(self) -> bytes:
        """Capture the CURRENT page as image ``bytes`` via ``client.screenshot``."""
        client = self._require_client()
        # a screenshot captures a rendered page → there IS a visual to show (browser-nav).
        self._mark_work("render")
        url = await self._nav.current_url()
        data = await self._guarded(client.screenshot(url=url))
        return bytes(data) if isinstance(data, (bytes, bytearray)) else str(data).encode()

    # --- the raw-input CDP-WS transport (the ONE non-SDK path; None until a session) ---
    @property
    def transport(self) -> CdpTransport | None:
        return self._transport


__all__ = ["GhostCrawlProvider", "ProviderCallError"]
