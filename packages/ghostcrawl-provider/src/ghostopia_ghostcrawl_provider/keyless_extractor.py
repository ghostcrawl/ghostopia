"""Deterministic KEYLESS field extractor (R4 /) — a ghostopia CONSUMER heuristic.

A keyless GhostCrawl ``/v1/scrape`` returns rendered content (markdown/HTML/text) with NO
server-side ``extracted`` — that structured layer needs the user's own BYO-LLM key. To ship
a working priced-card example out-of-box with ONLY a GhostCrawl key, ghostopia lifts that raw
content into a ``{title, price, image, link}`` record with pure, per-site-FREE heuristics:

* **price** — the first currency-shaped token (symbol-prefixed ``£51.77``/``$12.99`` or
  code-suffixed ``1,299.00 USD``).
* **image** — ``og:image`` meta, else the first markdown image ``![...](url)``, else the
  first ``<img src>`` — absolutized against the page URL.
* **title** — the first markdown heading, else ``<h1>``, else a JSON-LD ``"name"``, else
  ``<title>``.
* **link** — ALWAYS the page URL (the one field that is always available).

This is a DOWNSTREAM CONSUMER heuristic and lives in the ghostopia tree by design: it
is NOT a GhostCrawl capability and never belongs in the product tree. It uses only the Python
standard library — no third-party scraper (GhostCrawl is the sole AIO harness). For arbitrary
/ irregular targets the BYO path (the user's own LLM/MCP/AI) supersedes this by returning
server-side ``extracted``; see :mod:`ghostopia_ghostcrawl_provider.provider`.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urljoin, urlsplit

# Navigation "chrome" a listing page links to that is NOT a detail/product page: the site root
# / homepage, a category/listing index, or a pagination link. Following these pulls generic
# non-detail content (e.g. the homepage's first item) into every department, so they are dropped
# from the discovered candidate list. Structure-only (no hostname special case).
_NAV_PAGINATION_RE = re.compile(r"(?:[?&]page=\d+|/page[-/]\d+|_\d+/page)", re.IGNORECASE)

# A static asset (image / style / script / font / media) — never a product/detail page.
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
        return True  # the site root / homepage
    if "/category/" in path:
        return True  # a category / listing index, not a detail page
    if _NAV_PAGINATION_RE.search(url):
        return True  # pagination
    # any path segment that is universal store nav (account / cart / faq / …) → not a product.
    for seg in path.strip("/").split("/"):
        stem = seg.rsplit(".", 1)[0]  # drop a trailing .html/.aspx so "check-out.aspx" matches
        if stem in _NAV_SEGMENTS:
            return True
    return False

# --- price -----------------------------------------------------------------------------
# Symbol-prefixed (``£51.77``, ``$12.99``, ``€9,99``, ``¥1980``) OR code-suffixed
# (``1,299.00 USD``). Amounts allow thousands separators (``,`` or ``.``) and an optional
# fractional part. The raw matched token is returned verbatim.
_PRICE_RE = re.compile(
    r"[£$€¥₹]\s?\d[\d.,]*\d|[£$€¥₹]\s?\d"
    r"|\d[\d.,]*\d\s?(?:USD|EUR|GBP|JPY|INR|CAD|AUD)",
    re.IGNORECASE,
)
# A bare numeric token inside a price string (for comparing two candidate prices by amount).
_PRICE_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")
# A STRUCTURED price (schema.org JSON-LD / microdata ``offers.price``) — the authoritative
# current price when the page carries machine-readable product data; preferred over a body token.
_STRUCT_PRICE_RE = re.compile(r'"price"\s*:\s*"?(\d[\d.,]*\d|\d)"?')
_STRUCT_CURRENCY_RE = re.compile(r'"priceCurrency"\s*:\s*"([A-Za-z]{3})"')
# Context words that mark a NON-current price just BEFORE a token (the old / reference / shipping
# / "from" price): such a token must never be taken as the product's current price.
_NON_CURRENT_RE = re.compile(
    r"\b(was|were|rrp|list|orig(?:inal)?|save|shipping|delivery|postage|from|starting|msrp|instead)\b",
    re.IGNORECASE,
)
# Strikethrough wrappers (markdown ``~~…~~`` / HTML ``<del>``/``<s>``/``<strike>``/CSS
# line-through) immediately before a token → a struck (old) price, skipped.
_STRIKE_PREFIX_RE = re.compile(r"(~~|<del\b|<s>|<strike\b|line-through[^>]*>)\s*[£$€¥₹]?\s*$", re.IGNORECASE)
# In-stock / condition markers (lifted when present so the ranker can prefer in-stock / new).
_OUT_OF_STOCK_RE = re.compile(r"\b(out of stock|sold out|unavailable|discontinued|pre[- ]?order)\b", re.IGNORECASE)
_IN_STOCK_RE = re.compile(r"\b(in stock|available|add to (?:cart|basket|bag))\b", re.IGNORECASE)
_USED_RE = re.compile(r"\b(used|refurbished|renewed|pre[- ]?owned|open box)\b", re.IGNORECASE)


def _price_token_amount(token: str) -> float | None:
    m = _PRICE_NUM_RE.search(token)
    if m is None:
        return None
    num = m.group(0)
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        num = num.replace(".", "").replace(",", ".") if num.rfind(",") > num.rfind(".") else num.replace(",", "")
    elif has_comma:
        num = num.replace(",", ".") if (num.count(",") == 1 and len(num.rsplit(",", 1)[1]) in (1, 2)) else num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None

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


def extract_price(text: str) -> str | None:
    """The product's CURRENT price, verbatim, or ``None``.

    Price source priority (R3): a STRUCTURED ``offers.price`` (schema.org JSON-LD / microdata)
    wins — it is the authoritative current price. Otherwise the body is scanned for currency
    tokens and the LOWEST NON-STRUCK current price is taken: tokens preceded by a strikethrough
    wrapper (``~~`` / ``<del>`` / ``<s>`` / ``<strike>`` / line-through) or a non-current marker
    ("was" / "RRP" / "from" / "shipping" / "save" / ...) are skipped, so a crossed-out original
    price, a shipping fee, or a "from £5" teaser never masquerades as the price. Per-site-free —
    structure only.
    """
    sm = _STRUCT_PRICE_RE.search(text)
    if sm:
        cur = _STRUCT_CURRENCY_RE.search(text)
        amount = sm.group(1)
        return f"{amount} {cur.group(1).upper()}" if cur else amount
    best_token: str | None = None
    best_amount: float | None = None
    for match in _PRICE_RE.finditer(text):
        pre = text[max(0, match.start() - 28):match.start()]
        if _NON_CURRENT_RE.search(pre) or _STRIKE_PREFIX_RE.search(pre):
            continue  # a struck / old / shipping / "from" price — never the current price
        amount = _price_token_amount(match.group(0))
        if amount is None:
            continue
        if best_amount is None or amount < best_amount:
            best_amount, best_token = amount, match.group(0).strip()
    return best_token


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
    (the server surfaces the page's links, ``/v1/scrape`` 2.3.6-253+) OR the legacy
    body-parse fallback below. This is genuine crawler POLICY, not extraction: keep only
    the links worth walking to reach priced detail pages —

      * absolutized against the page URL, http(s) only, de-duplicated in order;
      * SAME-HOST (a store's global nav links out to partner/CDN domains — stay on the
        store the department targets; when the base has no host, keep everything);
      * not the page itself, not an image/asset, not obvious navigation chrome
        (site root / a ``/category/`` index / cart / login / pagination).

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
            continue  # an image / script / stylesheet, not a detail page
        if _is_nav_chrome(absu):
            continue  # drop the site root / category index / pagination — keep detail pages
        seen.add(absu)
        out.append(absu)
    return out


def deterministic_extract(url: str, content: str | None) -> dict[str, Any]:
    """Lift raw scraped ``content`` into a ``{url, link, title?, price?, image?, content}`` record.

    ``link`` is ALWAYS the page URL; ``title``/``price``/``image`` are included only when a
    heuristic finds them (a card renders priced when ``title`` or ``price`` is present, else it
    falls back to the raw ``content`` blob — nothing is lost). Per-site-free: structure only,
    never a hostname special case.
    """
    text = content if isinstance(content, str) else ""
    record: dict[str, Any] = {"url": url, "link": url}
    title = extract_title(text)
    if title:
        record["title"] = title
    price = extract_price(text)
    if price:
        record["price"] = price
    image = extract_image(text, url)
    if image:
        record["image"] = image
    # Stock + condition signals (lifted ONLY when the copy states them) so the downstream
    # ranker can prefer an in-stock / new listing — structure only, never fabricated.
    if _OUT_OF_STOCK_RE.search(text):
        record["in_stock"] = False
    elif _IN_STOCK_RE.search(text):
        record["in_stock"] = True
    if _USED_RE.search(text):
        record["condition"] = "used"
    # keep the raw content so the blob fallback survives when no fields were found.
    record["content"] = text
    return record


__all__ = [
    "crawl_policy_filter",
    "deterministic_extract",
    "extract_image",
    "extract_price",
    "extract_title",
]
