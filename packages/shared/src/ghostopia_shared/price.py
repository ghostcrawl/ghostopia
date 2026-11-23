"""The ONE price parser (2026-10-18).

Before there were four drifting copies (``product_match.parse_price_amount``,
``db.parse_price``, ``pipeline_crawl._price_number``, ``keyless_extractor._price_token_amount``).
Every layer now imports this module, so they cannot disagree.

Rules (pure, stdlib, per-site-free):

* **Currency** — multi-char symbols are matched before bare ones (``CA$``/``A$``/``NZ$``/``HK$``/
  ``S$``/``MX$``/``R$``/``US$``/``CN¥`` before ``$``/``¥``), then ISO-4217 code words. A bare
  amount carries NO currency (``None``) unless the caller passes a ``currency_hint`` (e.g. a
  structured ``priceCurrency``); it is never silently assumed to be USD.
* **Number** — both grouping conventions (``1,299.00``/``1.299,00``/``9,99``); a single
  ``.``-group of exactly 3 digits is THOUSANDS for dot-grouping currencies (``€1.299`` → 1299).
* **Rejects** — ``<= 0``/``Free`` placeholders, multi-buy (``2 for $10``), instalments
  (``4 payments of $25``/``$33/mo``), and non-current context (``Was``/``RRP``/``MSRP``/
  ``from``/``shipping``/``delivery``/``save``/struck ``~~$30~~``). In ``Was $30 Now $20`` the
  FIRST current token (``$20``) wins — never the lowest token on a page.

:data:`STATIC_USD_RATES` is a small, static, APPROXIMATE ordering table (as of
:data:`FX_ASOF`) — a display convenience, never a settlement rate. An unknown currency
normalizes to ``None`` so it is excluded from cross-currency ordering.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

__all__ = [
    "FX_ASOF",
    "STATIC_USD_RATES",
    "PriceParse",
    "PriceToken",
    "detect_currency",
    "find_price_tokens",
    "locale_currency_for",
    "normalize_amount",
    "parse_price",
    "parse_price_amount",
    "to_float",
]

FX_ASOF = "2026-10 (approximate, static)"

STATIC_USD_RATES: dict[str, float] = {
    "USD": 1.0, "EUR": 1.08, "GBP": 1.27, "JPY": 0.0067, "CAD": 0.73, "AUD": 0.66,
    "NZD": 0.60, "CHF": 1.12, "CNY": 0.14, "INR": 0.012, "MXN": 0.058, "BRL": 0.19,
    "HKD": 0.128, "SGD": 0.74, "KRW": 0.00073, "SEK": 0.095, "NOK": 0.093, "DKK": 0.145,
    "PLN": 0.25, "ZAR": 0.055,
}

# Longest first so ``CA$`` never reads as ``$`` and ``CN¥`` never as ``¥``.
_SYMBOLS: tuple[tuple[str, str], ...] = (
    ("US$", "USD"), ("CA$", "CAD"), ("AU$", "AUD"), ("NZ$", "NZD"), ("HK$", "HKD"),
    ("MX$", "MXN"), ("CN¥", "CNY"), ("JP¥", "JPY"),
    ("C$", "CAD"), ("A$", "AUD"), ("S$", "SGD"), ("R$", "BRL"),
    ("£", "GBP"), ("€", "EUR"), ("₹", "INR"), ("₩", "KRW"), ("¥", "JPY"), ("$", "USD"),
)
_CODES: tuple[str, ...] = tuple(STATIC_USD_RATES) + ("RMB",)
_CODE_ALIAS = {"RMB": "CNY"}
# Currencies whose sites group thousands with ``.`` (``€1.299`` = 1299).
_DOT_GROUPING = frozenset({"EUR", "BRL", "DKK", "NOK", "SEK", "PLN", "CHF"})

_SYM_ALT = "|".join(re.escape(s) for s, _ in _SYMBOLS)
_CODE_ALT = "|".join(_CODES)
# Thousands may be grouped by space/NBSP/narrow NBSP/apostrophe (``1 299,00 €``,
# ``CHF 1'299.00``) — tried FIRST so the token is never truncated to its leading group (M4).
_GROUP_SEP = " \u00a0\u202f'\u2019"
_NUM = rf"\d{{1,3}}(?:[{_GROUP_SEP}]\d{{3}})+(?:[.,]\d{{1,2}})?|\d[\d.,]*\d|\d"
# Ambiguous symbols: a nearby ISO code or the page locale decides the currency (M3).
_DOLLAR_FAMILY = frozenset({"USD", "CAD", "AUD", "NZD", "HKD", "SGD", "MXN"})
_YEN_FAMILY = frozenset({"JPY", "CNY"})
_AMBIGUOUS = {"$": _DOLLAR_FAMILY, "¥": _YEN_FAMILY}
# A currency-marked price token: symbol-prefixed, code-prefixed or code-suffixed.
_TOKEN_RE = re.compile(
    rf"(?P<sym>{_SYM_ALT})\s?(?P<n1>{_NUM})"
    rf"|\b(?P<pcode>{_CODE_ALT})\s?(?P<n2>{_NUM})"
    rf"|(?P<n3>{_NUM})\s?(?P<scode>{_CODE_ALT})\b"
    rf"|(?P<n4>{_NUM})\s?(?P<ssym>€|£|₹|₩|¥)",
)
_BARE_NUM_RE = re.compile(_NUM)

# Context that marks a NON-current price just BEFORE a token.
_NON_CURRENT_PRE = re.compile(
    r"(?:\b(?:was|were|rrp|list(?:\s+price)?|orig(?:inal)?|save|saving|shipping|delivery|"
    r"postage|from|starting(?:\s+at)?|msrp|instead\s+of|compare\s+at|reg(?:ular)?)\b"
    r"|\bm\.?r\.?p\b\.?)[^\d\n]{0,12}$",
    re.IGNORECASE,
)
_STRIKE_OPEN_RE = re.compile(r"<(del|s|strike)\b[^>]*>", re.IGNORECASE)
_STRIKE_CLOSE_RE = re.compile(r"</(del|s|strike)\s*>", re.IGNORECASE)
_LINE_THROUGH_PRE = re.compile(r"line-through[^>]*>\s*[^\d<]{0,4}$", re.IGNORECASE)


def _is_struck(text: str, start: int) -> bool:
    """Is the token at ``start`` INSIDE an open strikethrough (``~~…``, ``<del>``/``<s>``)?"""
    line = text[text.rfind("\n", 0, start) + 1:start]
    if line.count("~~") % 2 == 1:
        return True
    pre = text[max(0, start - 120):start]
    opens = list(_STRIKE_OPEN_RE.finditer(pre))
    if opens:
        closes = list(_STRIKE_CLOSE_RE.finditer(pre))
        if not closes or closes[-1].start() < opens[-1].start():
            return True
    return bool(_LINE_THROUGH_PRE.search(pre))
_INSTALMENT_PRE = re.compile(
    r"\b(payments?|instal(?:l)?ments?)\s+of\s*$|\b\d+\s*x\s*$", re.IGNORECASE
)
_INSTALMENT_POST = re.compile(r"^\s*(/\s?(mo|month|wk|week)\b|per\s+(month|week)|a\s+month|monthly)", re.IGNORECASE)
_MULTIBUY_PRE = re.compile(r"\b\d+\s*(for|/)\s*$", re.IGNORECASE)
_MULTIBUY_POST = re.compile(r"^\s*(each\s+)?(when\s+you\s+buy|for\s+\d+)", re.IGNORECASE)
_FREE_RE = re.compile(r"\bfree\b", re.IGNORECASE)
# A per-UNIT price (``$1.20/oz``, ``£3.50 per kg``) is never the item price.
_PER_UNIT_POST = re.compile(
    r"^\s?\)?\s?(?:/|per\s+|a\s+)\s?(?:\d+\s?)?(oz|ounce|fl\s?oz|lb|lbs|pound|kg|g|gram|100\s?g|ml|l|"
    r"litre|liter|ct|count|each|ea|unit|item|sheet|roll|sq\.?\s?ft|ft|m|metre|meter|yd|load|wash)\b",
    re.IGNORECASE,
)
# A price RANGE (``$10–$20``, ``$9.99 - $19.99``, ``$10 to $20``) is not a single price.
_RANGE_POST = re.compile(rf"^\s?(?:–|—|-|to)\s?(?:{_SYM_ALT})?\s?\d", re.IGNORECASE)
_RANGE_PRE = re.compile(rf"\d\s?(?:–|—|-|to)\s?(?:{_SYM_ALT})?\s?$", re.IGNORECASE)
_CODE_POST = re.compile(rf"^\s?({_CODE_ALT})\b")
_CODE_PRE = re.compile(rf"\b({_CODE_ALT})\s?$")


def to_float(num: str, currency: str | None = None) -> float:
    """A grouped numeric token → float (both conventions; dot-thousands for EUR-style;
    space/NBSP/apostrophe thousands)."""
    num = re.sub(f"[{_GROUP_SEP}]", "", num)
    has_dot, has_comma = "." in num, "," in num
    if has_dot and has_comma:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif has_comma:
        if num.count(",") == 1 and len(num.rsplit(",", 1)[1]) in (1, 2):
            num = num.replace(",", ".")
        else:
            num = num.replace(",", "")
    elif has_dot:
        groups = num.split(".")
        if len(groups) > 2 or (
            (currency or "").upper() in _DOT_GROUPING and len(groups[1]) == 3
        ):
            num = num.replace(".", "")
    return float(num)


def detect_currency(text: str) -> str | None:
    """The currency a price string names (multi-char symbol > symbol > ISO code), else None."""
    for sym, code in _SYMBOLS:
        if sym in text:
            return code
    m = re.search(rf"\b({_CODE_ALT})\b", text.upper())
    return _CODE_ALIAS.get(m.group(1), m.group(1)) if m else None


@dataclass(frozen=True)
class PriceToken:
    start: int
    end: int
    raw: str
    amount: float | None
    currency: str | None
    #: why the token is NOT a current price (None = acceptable).
    reject: str | None


@dataclass(frozen=True)
class PriceParse:
    amount: float | None
    currency: str | None
    #: None when ``amount`` is usable, else the reject reason.
    reason: str | None = None
    raw: str | None = None

    @property
    def ok(self) -> bool:
        return self.amount is not None


def _classify(text: str, start: int, end: int, amount: float | None) -> str | None:
    pre = text[max(0, start - 40):start]
    post = text[end:end + 24]
    if amount is None:
        return "no_number"
    if _is_struck(text, start):
        return "struck"
    if _INSTALMENT_PRE.search(pre) or _INSTALMENT_POST.search(post):
        return "instalment"
    if _MULTIBUY_PRE.search(pre) or _MULTIBUY_POST.search(post):
        return "multi_buy"
    if _PER_UNIT_POST.search(post):
        return "per_unit"
    if _RANGE_POST.search(post) or _RANGE_PRE.search(pre):
        return "range"
    if _NON_CURRENT_PRE.search(pre):
        return "non_current"
    if amount <= 0:
        return "non_positive"
    return None


def find_price_tokens(text: str, *, locale_currency: str | None = None) -> list[PriceToken]:
    """Every currency-marked price token in ``text``, in document order, each classified.

    An ambiguous symbol (``$``/``¥``) is resolved by an adjacent ISO code (``$99 CAD``,
    ``CAD $99``), else by ``locale_currency`` (the page's locale/TLD currency) when it is in the
    symbol's family, else the default (USD/JPY)."""
    loc = locale_currency.upper() if isinstance(locale_currency, str) and locale_currency else None
    out: list[PriceToken] = []
    for m in _TOKEN_RE.finditer(text):
        if m.group("sym"):
            sym = m.group("sym")
            cur = dict(_SYMBOLS)[sym]
            num = m.group("n1")
            post_code = _CODE_POST.match(text[m.end():m.end() + 6])
            pre_code = _CODE_PRE.search(text[max(0, m.start() - 5):m.start()])
            code = (post_code or pre_code)
            if code is not None:
                cur = _CODE_ALIAS.get(code.group(1), code.group(1))
            elif sym in _AMBIGUOUS and loc in _AMBIGUOUS[sym]:
                cur = loc
        elif m.group("pcode"):
            cur, num = m.group("pcode"), m.group("n2")
        elif m.group("scode"):
            cur, num = m.group("scode"), m.group("n3")
        else:
            cur, num = dict(_SYMBOLS)[m.group("ssym")], m.group("n4")
            if m.group("ssym") == "¥" and loc in _YEN_FAMILY:
                cur = loc
        cur = _CODE_ALIAS.get(cur.upper(), cur.upper())
        try:
            amount: float | None = to_float(num, cur)
        except ValueError:
            amount = None
        out.append(
            PriceToken(m.start(), m.end(), m.group(0).strip(), amount, cur,
                       _classify(text, m.start(), m.end(), amount))
        )
    return out


def parse_price(
    raw: Any, *, currency_hint: str | None = None, locale_currency: str | None = None
) -> PriceParse:
    """Parse ONE price value (a field string or a structured number) → :class:`PriceParse`.

    The first CURRENT currency-marked token wins; with no marked token a bare number is read
    (currency = ``currency_hint`` or None). ``<= 0``/Free/multi-buy/instalment /
    non-current values are rejected (``amount=None`` + ``reason``)."""
    hint = currency_hint.strip().upper() if isinstance(currency_hint, str) and currency_hint.strip() else None
    if isinstance(raw, bool) or raw is None:
        return PriceParse(None, None, "empty")
    if isinstance(raw, (int, float)):
        amt = float(raw)
        if amt != amt or amt <= 0:  # NaN or non-positive
            return PriceParse(None, hint, "non_positive", str(raw))
        return PriceParse(amt, hint, None, str(raw))
    if not isinstance(raw, str):
        return PriceParse(None, None, "empty")
    text = raw.strip()
    if not text:
        return PriceParse(None, None, "empty")
    tokens = find_price_tokens(text, locale_currency=locale_currency)
    if tokens:
        for tok in tokens:
            if tok.reject is None:
                return PriceParse(tok.amount, hint or tok.currency, None, tok.raw)
        return PriceParse(None, hint or tokens[0].currency, tokens[0].reject, text)
    m = _BARE_NUM_RE.search(text)
    if m is None:
        return PriceParse(None, hint, "free" if _FREE_RE.search(text) else "no_number", text)
    cur = hint or detect_currency(text) or (
        locale_currency.upper() if isinstance(locale_currency, str) and locale_currency else None
    )
    try:
        amount = to_float(m.group(0), cur)
    except ValueError:
        return PriceParse(None, cur, "no_number", text)
    reason = _classify(text, m.start(), m.end(), amount)
    if reason is not None:
        return PriceParse(None, cur, reason, text)
    return PriceParse(amount, cur, None, text)


def parse_price_amount(raw: Any, *, currency_hint: str | None = None) -> tuple[float | None, str | None]:
    """Tuple form of :func:`parse_price` — ``(amount, currency)``; ``amount`` None = unusable."""
    p = parse_price(raw, currency_hint=currency_hint)
    return (p.amount, p.currency)


_TLD_CURRENCY: tuple[tuple[str, str], ...] = (
    (".com.au", "AUD"), (".au", "AUD"), (".co.nz", "NZD"), (".nz", "NZD"), (".ca", "CAD"),
    (".com.mx", "MXN"), (".mx", "MXN"), (".com.sg", "SGD"), (".sg", "SGD"), (".com.hk", "HKD"),
    (".hk", "HKD"), (".cn", "CNY"), (".com.cn", "CNY"), (".jp", "JPY"), (".co.jp", "JPY"),
)
_LANG_CURRENCY = {"en-ca": "CAD", "fr-ca": "CAD", "en-au": "AUD", "en-nz": "NZD", "es-mx": "MXN",
                  "zh-cn": "CNY", "zh-hans": "CNY", "ja": "JPY", "ja-jp": "JPY", "en-sg": "SGD",
                  "zh-hk": "HKD"}
_LANG_RE = re.compile(
    r'<html[^>]*\blang=["\']([A-Za-z-]+)["\']'
    r'|og:locale["\'][^>]*content=["\']([A-Za-z_-]+)["\']',
    re.IGNORECASE,
)


def locale_currency_for(url: str | None, text: str | None = None) -> str | None:
    """The page's locale currency (``<html lang>``/``og:locale``, else the host TLD), used ONLY
    to resolve an ambiguous ``$``/``¥``. ``None`` when the page gives no locale signal."""
    if isinstance(text, str) and text:
        m = _LANG_RE.search(text[:20000])
        if m:
            lang = (m.group(1) or m.group(2) or "").lower().replace("_", "-")
            if lang in _LANG_CURRENCY:
                return _LANG_CURRENCY[lang]
    if isinstance(url, str) and url:
        host = (urlsplit(url).hostname or "").lower()
        for tld, cur in _TLD_CURRENCY:
            if host.endswith(tld):
                return cur
    return None


def normalize_amount(amount: float | None, currency: str | None) -> float | None:
    """``amount`` → approximate USD via :data:`STATIC_USD_RATES`.

    ``None`` for a missing amount OR an unknown/absent currency — such an offer is excluded
    from cross-currency ordering rather than silently treated as USD."""
    if amount is None:
        return None
    rate = STATIC_USD_RATES.get((currency or "").upper())
    return None if rate is None else amount * rate
