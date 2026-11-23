"""Deterministic KEYLESS field extractor — a ghostopia CONSUMER heuristic.

A keyless GhostCrawl ``/v1/scrape`` returns rendered content (markdown/HTML/text) with NO
server-side ``extracted`` — that structured layer needs the user's own BYO-LLM key. To ship
a working priced-card example out-of-box with ONLY a GhostCrawl key, ghostopia lifts that raw
content into a ``{title, price, image, link}`` record with pure, per-site-FREE heuristics:

* **price** — STRUCTURED first (JSON-LD ``Product.offers``/microdata/``product:price``
  meta, with ``priceCurrency``/``availability``/``itemCondition``/GTIN/MPN); else the
  current price token nearest the product title (shared parser, ``ghostopia_shared.price``).
  ``price_method`` records which path produced it.
* **image** — ``og:image`` meta, else the first markdown image ``![...](url)``, else the
  first ``<img src>`` — absolutized against the page URL.
* **title** — the first markdown heading, else ``<h1>``, else a JSON-LD ``"name"``, else
  ``<title>``.
* **link** — ALWAYS the page URL (the one field that is always available).

This is a DOWNSTREAM CONSUMER heuristic and lives in the ghostopia tree by design : it
is NOT a GhostCrawl capability and never belongs in the product tree. It uses only the Python
standard library — no third-party scraper (GhostCrawl is the sole AIO harness). For arbitrary
/ irregular targets the BYO path (the user's own LLM/MCP/AI) supersedes this by returning
server-side ``extracted``; see :mod:`ghostopia_ghostcrawl_provider.provider`.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urljoin, urlsplit

from ghostopia_shared.price import find_price_tokens, locale_currency_for, parse_price_amount

# Navigation "chrome" a listing page links to that is NOT a detail/product page: the site root
#/homepage, a category/listing index, or a pagination link. Following these pulls generic
# non-detail content (e.g. the homepage's first item) into every department, so they are dropped
# from the discovered candidate list. Structure-only (no hostname special case).
_NAV_PAGINATION_RE = re.compile(r"(?:[?&]page=\d+|/page[-/]\d+|_\d+/page)", re.IGNORECASE)

# A static asset (image/style/script/font/media) — never a product/detail page.
_ASSET_RE = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|css|js|mjs|woff2?|ttf|eot|pdf|mp4|webm|zip)(?:$|\?)",
    re.IGNORECASE,
)

# Universal e-commerce navigation paths that are NEVER a product/detail page — the account/cart/
# help chrome every store shares. Cross-site by PATTERN (not a per-site hostname rule): a path
# segment equal to one of these is site plumbing, so it is dropped from the candidate list.
_NAV_SEGMENTS = frozenset(
    {
        "account", "accounts", "cart", "carts", "checkout", "check-out", "login", "signin",
        "sign-in", "logout", "signout", "register", "signup", "sign-up", "wishlist",
        "save-for-later", "order-history", "orders", "faq", "faqs", "help", "support",
        "about", "about-us", "contact", "contact-us", "privacy", "terms", "shipping",
        "returns", "return-policy", "blog", "news", "search", "pre-order", "gift-card",
        "gift-cards", "gift-certificate", "customer-service", "track-order", "sitemap",
        "frequently-asked-questions", "faqs-help", "how-to", "size-chart", "size-guide",
        "store-locator", "stores", "reviews", "wish-list", "my-account", "order-status",
    }
)


def _is_nav_chrome(url: str) -> bool:
    parts = urlsplit(url)
    path = (parts.path or "/").lower()
    if path in ("", "/", "/index.html", "/index.htm", "/index.php"):
        return True  # the site root/homepage
    if "/category/" in path:
        return True  # a category/listing index, not a detail page
    if _NAV_PAGINATION_RE.search(url):
        return True  # pagination
    # any path segment that is universal store nav (account/cart/faq/…) → not a product.
    for seg in path.strip("/").split("/"):
        stem = seg.rsplit(".", 1)[0]  # drop a trailing .html/.aspx so "check-out.aspx" matches
        if stem in _NAV_SEGMENTS:
            return True
    return False

# --- price -----------------------------------------------------------------------------
# All price parsing goes through the ONE shared parser . The keyless path prefers
# STRUCTURED product data (JSON-LD ``Product.offers``/microdata ``itemprop=price`` /
# ``product:price:*`` meta) and only then falls back to the body token NEAREST the product title
# — never the global-minimum token on the page, which picks add-ons, carousels and instalments
# . Stock/condition come ONLY from structured data; body copy ("commonly used",
# "available") is never read as a stock/condition signal .
_JSONLD_SCRIPT_RE = re.compile(
    r"<script[^>]+application/ld\+json[^>]*>(.*?)</script>", re.IGNORECASE | re.DOTALL
)
# A bare JSON object that names a schema.org type (JSON-LD surviving as text in a markdown scrape).
_JSON_OBJECT_START_RE = re.compile(r'\{\s*"@(?:context|type|graph)"')
_MICRO_PRICE_RE = re.compile(
    r'itemprop=["\']price["\'][^>]*?content=["\']([^"\']+)["\']'
    r'|content=["\']([^"\']+)["\'][^>]*?itemprop=["\']price["\']',
    re.IGNORECASE,
)
_MICRO_CURRENCY_RE = re.compile(
    r'itemprop=["\']priceCurrency["\'][^>]*?content=["\']([A-Za-z]{3})["\']'
    r'|content=["\']([A-Za-z]{3})["\'][^>]*?itemprop=["\']priceCurrency["\']',
    re.IGNORECASE,
)
_MICRO_AVAIL_RE = re.compile(
    r'itemprop=["\']availability["\'][^>]*?(?:href|content)=["\']([^"\']+)["\']', re.IGNORECASE
)
_MICRO_COND_RE = re.compile(
    r'itemprop=["\']itemCondition["\'][^>]*?(?:href|content)=["\']([^"\']+)["\']', re.IGNORECASE
)
_META_PRICE_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:product|og):price:amount["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_CURRENCY_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\'](?:product|og):price:currency["\'][^>]+content=["\']([A-Za-z]{3})["\']',
    re.IGNORECASE,
)
_GTIN_KEYS = ("gtin13", "gtin12", "gtin14", "gtin8", "gtin", "ean", "upc", "isbn")

_AVAIL_IN = ("instock", "limitedavailability", "onlineonly", "instoreonly", "preorder", "presale", "backorder")
_AVAIL_OUT = ("outofstock", "soldout", "discontinued")


def _availability(val: Any) -> bool | None:
    if not isinstance(val, str) or not val.strip():
        return None
    low = val.strip().rsplit("/", 1)[-1].lower().replace(" ", "").replace("_", "")
    if any(k in low for k in _AVAIL_OUT):
        return False
    if any(k in low for k in _AVAIL_IN):
        return True
    return None


def _condition(val: Any) -> str | None:
    if not isinstance(val, str) or not val.strip():
        return None
    low = val.strip().rsplit("/", 1)[-1].lower()
    if "new" in low:
        return "new"
    if "refurb" in low:
        return "refurbished"
    if "used" in low or "damaged" in low:
        return "used"
    return None


def _json_objects(text: str) -> list[Any]:
    """Every JSON-LD payload in ``text`` (``<script>`` blocks, then bare ``{"@type":...}`` text)."""
    out: list[Any] = []
    for m in _JSONLD_SCRIPT_RE.finditer(text):
        try:
            out.append(json.loads(m.group(1).strip()))
        except (json.JSONDecodeError, ValueError):
            continue
    if out:
        return out
    dec = json.JSONDecoder()
    for m in _JSON_OBJECT_START_RE.finditer(text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except (json.JSONDecodeError, ValueError):
            continue
        out.append(obj)
    return out


def _walk_products(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, list):
        for n in node:
            found.extend(_walk_products(n))
    elif isinstance(node, dict):
        typ = node.get("@type")
        types = typ if isinstance(typ, list) else [typ]
        if any(isinstance(t, str) and t.lower() in ("product", "productgroup") for t in types):
            found.append(node)
        elif "offers" in node and isinstance(node.get("name"), str) and "@type" not in node:
            found.append(node)
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found.extend(_walk_products(node[key]))
    return found


def _offer_price(offer: dict[str, Any]) -> tuple[float | None, str | None]:
    cur = offer.get("priceCurrency")
    spec = offer.get("priceSpecification")
    if isinstance(spec, list):
        spec = spec[0] if spec else None
    for raw in (offer.get("price"), offer.get("lowPrice"),
                spec.get("price") if isinstance(spec, dict) else None):
        if raw is None:
            continue
        if not cur and isinstance(spec, dict):
            cur = spec.get("priceCurrency")
        amount: float | None
        try:
            amount = float(raw) if isinstance(raw, (int, float)) else float(str(raw).replace(",", ""))
        except ValueError:
            amount, parsed_cur = parse_price_amount(raw)
            cur = cur or parsed_cur
        if amount is not None and amount > 0:
            return amount, (cur.strip().upper() if isinstance(cur, str) and cur.strip() else None)
    return None, None


def extract_structured(text: str) -> dict[str, Any] | None:
    """The main Product's structured fields (JSON-LD → microdata → product meta), else None.

    Returns ``{price_num, currency, in_stock?, condition?, title?, image?, gtin?, mpn?, brand?,
    price_method}``. The FIRST ``Product`` node with a positive offer price wins (a listing's
    ``ItemList`` is not a product page)."""
    for obj in _json_objects(text):
        for prod in _walk_products(obj):
            offers = prod.get("offers")
            raw_offers = offers if isinstance(offers, list) else [offers]
            offer_list: list[dict[str, Any]] = [o for o in raw_offers if isinstance(o, dict)]
            best: tuple[float, str | None, dict[str, Any]] | None = None
            for off in offer_list:
                amount, cur = _offer_price(off)
                if amount is None:
                    continue
                if _availability(off.get("availability")) is False and best is not None:
                    continue
                if best is None or (amount < best[0] and _availability(off.get("availability")) is not False):
                    best = (amount, cur, off)
            if best is None:
                continue
            amount, cur, off = best
            rec: dict[str, Any] = {"price_num": amount, "currency": cur, "price_method": "jsonld"}
            stock = _availability(off.get("availability"))
            if stock is not None:
                rec["in_stock"] = stock
            cond = _condition(off.get("itemCondition") or prod.get("itemCondition"))
            if cond is not None:
                rec["condition"] = cond
            if isinstance(prod.get("name"), str) and prod["name"].strip():
                rec["title"] = prod["name"].strip()[:120]
            img = prod.get("image")
            if isinstance(img, list):
                img = img[0] if img else None
            if isinstance(img, dict):
                img = img.get("url")
            if isinstance(img, str) and img.strip():
                rec["image"] = img.strip()
            for k in _GTIN_KEYS:
                v = prod.get(k) or off.get(k)
                if isinstance(v, (str, int)) and re.fullmatch(r"\d{8,14}", str(v).strip()):
                    rec["gtin"] = str(v).strip()
                    break
            mpn = prod.get("mpn") or off.get("mpn")
            if isinstance(mpn, str) and mpn.strip():
                rec["mpn"] = mpn.strip()
            brand = prod.get("brand")
            if isinstance(brand, dict):
                brand = brand.get("name")
            if isinstance(brand, str) and brand.strip():
                rec["brand"] = brand.strip()
            return rec
    pm = _MICRO_PRICE_RE.search(text)
    if pm:
        cm = _MICRO_CURRENCY_RE.search(text)
        cur = next((g for g in cm.groups() if g), None) if cm else None
        amount, parsed_cur = parse_price_amount(next(g for g in pm.groups() if g), currency_hint=cur)
        if amount is not None:
            rec = {"price_num": amount, "currency": cur.upper() if cur else parsed_cur,
                   "price_method": "microdata"}
            am = _MICRO_AVAIL_RE.search(text)
            if am and _availability(am.group(1)) is not None:
                rec["in_stock"] = _availability(am.group(1))
            cdm = _MICRO_COND_RE.search(text)
            if cdm and _condition(cdm.group(1)) is not None:
                rec["condition"] = _condition(cdm.group(1))
            return rec
    mm = _META_PRICE_RE.search(text)
    if mm:
        cm = _META_CURRENCY_RE.search(text)
        cur = cm.group(1).upper() if cm else None
        amount, parsed_cur = parse_price_amount(mm.group(1), currency_hint=cur)
        if amount is not None:
            return {"price_num": amount, "currency": cur or parsed_cur, "price_method": "meta"}
    return None


def _title_offset(text: str) -> int:
    """Where the product title starts (first markdown heading/``<h1>``), else 0."""
    m = re.search(r"^\s{0,3}#{1,3}\s+\S", text, re.MULTILINE)
    h = _H1_RE.search(text)
    cands = [x.start() for x in (m, h) if x is not None]
    return min(cands) if cands else 0


def extract_price_token(
    text: str, url: str | None = None
) -> tuple[str, float, str | None] | None:
    """The body price token NEAREST (after) the product title: ``(raw, amount, currency)``.

    Tokens that are struck/non-current (was/RRP/from/shipping/save)/instalment/multi-buy /
    ``<= 0`` are skipped. The first acceptable token at or after the title wins; with none after
    the title, the first acceptable token anywhere. Never the page-wide minimum."""
    loc = locale_currency_for(url, text)
    tokens = [
        t for t in find_price_tokens(text, locale_currency=loc)
        if t.reject is None and t.amount is not None
    ]
    if not tokens:
        return None
    anchor = _title_offset(text)
    after = [t for t in tokens if t.start >= anchor]
    tok = (after or tokens)[0]
    return tok.raw, float(tok.amount), tok.currency  # type: ignore[arg-type]


def extract_price(text: str) -> str | None:
    """The product's CURRENT price as a display string, or ``None`` (structured first)."""
    st = extract_structured(text)
    if st is not None:
        cur = st.get("currency")
        return f"{st['price_num']:.2f} {cur}" if cur else f"{st['price_num']:.2f}"
    tok = extract_price_token(text)
    return tok[0] if tok else None


# --- image -----------------------------------------------------------------------------
_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)")
_IMG_TAG_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.IGNORECASE)

# --- title -----------------------------------------------------------------------------
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_TITLE_TAG_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_JSONLD_NAME_RE = re.compile(r'"name"\s*:\s*"([^"]+)"')
_TAG_STRIP_RE = re.compile(r"<[^>]+>")


def _first_markdown_heading(text: str) -> str | None:
    """The first markdown heading (``#``/``##``/``###``) text, unwrapping a ``[t](u)`` link."""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            body = stripped.lstrip("#").strip()
            body = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", body)
            body = body.replace("`", "").replace("*", "").strip()
            if body:
                return body[:120]
    return None


def extract_image(text: str, base_url: str) -> str | None:
    """The best product-image URL (og:image → markdown image → first ``<img>``), absolutized."""
    for rx in (_OG_IMAGE_RE, _MD_IMAGE_RE, _IMG_TAG_RE):
        match = rx.search(text)
        if match:
            src = next((g for g in match.groups() if g), None)
            if src:
                return urljoin(base_url, src.strip())
    return None


def extract_title(text: str) -> str | None:
    """A readable title: markdown heading → ``<h1>`` → JSON-LD ``name`` → ``<title>``."""
    heading = _first_markdown_heading(text)
    if heading:
        return heading
    for rx in (_H1_RE, _JSONLD_NAME_RE, _TITLE_TAG_RE):
        match = rx.search(text)
        if match:
            title = _TAG_STRIP_RE.sub("", match.group(1)).strip()
            if title:
                return title[:120]
    return None


def crawl_policy_filter(urls: Any, base_url: str) -> list[str]:
    """The ghost crawl POLICY: which of a page's links its ghosts actually follow.

    Applied to a link list from ANY source — GhostCrawl's structured ``discovered_urls``
    (the server surfaces the page's links, ``/v1/scrape`` 2.3.8+) OR the legacy
    body-parse fallback below. This is genuine crawler POLICY, not extraction: keep only
    the links worth walking to reach priced detail pages —

      * absolutized against the page URL, http(s) only, de-duplicated in order;
      * SAME-HOST (a store's global nav links out to partner/CDN domains — stay on the
        store the department targets; when the base has no host, keep everything);
      * not the page itself, not an image/asset, not obvious navigation chrome
        (site root/a ``/category/`` index/cart/login/pagination).

    GhostCrawl OWNS extracting the links a page carries; ghostopia OWNS which of them its
    ghosts walk. NO per-site logic — same-host + link shape only (consumer choice).
    """
    base = str(base_url or "").strip()
    base_key = base.rstrip("/")
    base_host = (urlsplit(base).hostname or "").lower()
    seen: set[str] = set()
    out: list[str] = []
    for raw in urls or []:
        if not isinstance(raw, str) or not raw.strip():
            continue
        absu = urljoin(base, raw.strip()) if base else raw.strip()
        absu = absu.split("#", 1)[0]  # drop the fragment (same page, different anchor)
        if not absu.startswith(("http://", "https://")):
            continue
        if absu.rstrip("/") == base_key or absu in seen:
            continue
        if base_host and (urlsplit(absu).hostname or "").lower() != base_host:
            continue
        if _ASSET_RE.search(absu):
            continue  # an image/script/stylesheet, not a detail page
        if _is_nav_chrome(absu):
            continue  # drop the site root/category index/pagination — keep detail pages
        seen.add(absu)
        out.append(absu)
    return out


def deterministic_extract(url: str, content: str | None, *, html: str | None = None) -> dict[str, Any]:
    """Lift raw scraped ``content`` into a ``{url, link, title?, price?, image?, content}`` record.

    ``link`` is ALWAYS the page URL; ``title``/``price``/``image`` are included only when a
    heuristic finds them (a card renders priced when ``title`` or ``price`` is present, else it
    falls back to the raw ``content`` blob — nothing is lost). Per-site-free: structure only,
    never a hostname special case.
    """
    text = content if isinstance(content, str) else ""
    if isinstance(html, str) and html.strip() and html not in text:
        struct_src = html + "\n" + text
    else:
        struct_src = text
    record: dict[str, Any] = {"url": url, "link": url}
    structured = extract_structured(struct_src)
    title = extract_title(text) or (structured or {}).get("title")
    if title:
        record["title"] = title
    if structured is not None:
        cur = structured.get("currency")
        amt = structured["price_num"]
        record["price"] = f"{amt:.2f} {cur}" if cur else f"{amt:.2f}"
        record["price_num"] = amt
        record["currency"] = cur
        record["price_method"] = structured["price_method"]
        # Stock/condition/identifiers ONLY from structured data .
        for k in ("in_stock", "condition", "gtin", "mpn", "brand"):
            if k in structured:
                record[k] = structured[k]
    else:
        tok = extract_price_token(text, url)
        if tok is not None:
            record["price"] = tok[0]
            record["price_num"] = tok[1]
            record["currency"] = tok[2]
            record["price_method"] = "title_nearest_token"
    image = extract_image(text, url) or (
        urljoin(url, structured["image"]) if structured and structured.get("image") else None
    )
    if image:
        record["image"] = image
    # keep the raw content so the blob fallback survives when no fields were found.
    record["content"] = text
    return record


__all__ = [
    "crawl_policy_filter",
    "deterministic_extract",
    "extract_image",
    "extract_price",
    "extract_price_token",
    "extract_structured",
    "extract_title",
]
