"""Deterministic product matching + offer ranking — the flagship cheapest-EXACT-product core.

The flagship ghostopia use case is "find the cheapest listing of the EXACT product I
searched". A naive crawler that just takes the global minimum price across a category page
returns the cheapest UNRELATED item (or an accessory). This module is the shared, per-site-FREE
brain that makes the result correct and is used from BOTH the crawl behaviors (the live
``result.verified`` best pick) AND the server's best-offer store (``db._product_key`` identity +
``best_offers`` ranking), so the two never disagree.

It is PURE and deterministic — standard library only, NO LLM, NO third party, NO network, NO
per-site/per-hostname rule. Everything keys on the SHAPE of the query and candidate titles:

1. :func:`parse_query` — understand the search target → ``{brand?, model codes, attribute
   tokens, all tokens}`` with case/whitespace/punctuation/diacritic normalization.
2. :func:`match_candidate` — score ONE candidate title against the target. The STRONG signal is a
   model-number exact/substring match; brand match + attribute token-set overlap refine it.
   Accessory listings ("case", "cover", "for ...", "compatible", "replacement", ...) are
   REJECTED outright. A candidate is "the same product" only if its score clears the threshold
   (tunable: arg ``threshold`` / env ``GHOSTOPIA_MATCH_THRESHOLD`` / default
   :data:`DEFAULT_MATCH_THRESHOLD`).
3. :func:`parse_offer` — read a candidate record into a comparable :class:`Offer` (amount +
   currency + in-stock + condition), preferring a STRUCTURED price over a heuristic token.
4. :func:`rank_offers` — among ONLY same-product candidates, group by currency, prefer in-stock,
   NEVER let a price-less record win, and pick the true minimum. Cross-currency ordering uses a
   small static USD rate table (see :data:`STATIC_USD_RATES` — approximate, clearly a
   convenience, documented below).
5. :func:`product_identity_key` — the stable identity a best-offer row is keyed on (model code
   signature, else a normalized token-set signature) so the SAME product across retailers and
   across slightly different titles collapses to one row.

An OPTIONAL AI seam is left open: :func:`rank_offers` accepts a ``rerank`` callable
(:data:`MatchReranker`) that an operator-connected brain (e.g. the AnthropicAgentProvider) can
supply to re-order / confirm the same-product set. It defaults to ``None`` — the deterministic
path works fully with NO API key.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "DEFAULT_MATCH_THRESHOLD",
    "STATIC_USD_RATES",
    "Target",
    "MatchResult",
    "Offer",
    "RankResult",
    "MatchReranker",
    "parse_query",
    "match_candidate",
    "parse_offer",
    "rank_offers",
    "product_identity_key",
    "normalize_amount",
    "parse_price_amount",
]

# The default same-product acceptance threshold (0..1). A candidate scores ABOVE this to count as
# "the same product the operator searched". Override per call (``threshold=``) or globally via the
# ``GHOSTOPIA_MATCH_THRESHOLD`` env var (an operator knob, no redeploy). Chosen so a model-number
# match always clears it and a bare 50% query-token overlap is exactly on the line.
DEFAULT_MATCH_THRESHOLD = 0.5


def _env_threshold(fallback: float) -> float:
    raw = os.environ.get("GHOSTOPIA_MATCH_THRESHOLD")
    if not raw:
        return fallback
    try:
        val = float(raw)
    except ValueError:
        return fallback
    return val if 0.0 <= val <= 1.0 else fallback


# --- CURRENCY DECISION -----------------------------------------------------------------------
# Ranking is CURRENCY-SAFE: offers are grouped by currency and the cheapest is picked WITHIN a
# single currency, NEVER by comparing bare numbers across currencies (so €9 can never "beat"
# £10). To still order a MIXED-currency list cheapest-first, amounts are converted to a common
# unit (USD) with this SMALL, STATIC, APPROXIMATE rate table. It is a display/ordering
# convenience ONLY — NOT a live FX feed, NOT a settlement rate; it is deliberately coarse and
# rarely churns. When a caller passes ``prefer_currency`` the winner is chosen from that currency
# bucket first (the operator's own currency), and the static table only breaks ties / orders the
# remainder. A currency with no entry falls back to a 1:1 USD assumption (documented, never a
# silent wrong-currency win because same-currency grouping still holds).
STATIC_USD_RATES: dict[str, float] = {
    "USD": 1.0,
    "EUR": 1.08,
    "GBP": 1.27,
    "JPY": 0.0067,
    "CAD": 0.73,
    "AUD": 0.66,
    "CHF": 1.12,
    "CNY": 0.14,
    "INR": 0.012,
    "MXN": 0.058,
    "BRL": 0.19,
}

# Currency symbol / ISO code detection (mirrors db.parse_price; kept here so the module is
# self-contained and importable with zero ghostopia deps).
_CURRENCY_SYMBOLS: dict[str, str] = {"£": "GBP", "$": "USD", "€": "EUR", "¥": "JPY", "₹": "INR"}
_CURRENCY_CODES: tuple[str, ...] = (
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "INR", "MXN", "BRL",
)
# A grouped/decimal numeric token in EITHER convention (US ``1,299.00`` / European ``1.299,00``
# / ``9,99``). Starts AND ends on a digit so a trailing sentence period is never absorbed.
_NUM_RE = re.compile(r"\d[\d.,]*\d|\d")

# Accessory / companion markers: a title carrying one of these (as a whole word, or ``for <x>``)
# is a companion item (a case / cover / cable for the product), NOT the product itself — rejected
# even when it names the exact model. Per-category-free: these are universal commerce words.
_ACCESSORY_MARKERS: frozenset[str] = frozenset(
    {
        "case", "cases", "cover", "covers", "skin", "skins", "protector", "protectors",
        "screenprotector", "strap", "straps", "band", "bands", "stand", "mount", "holder",
        "replacement", "compatible", "adapter", "adaptor", "charger", "cable", "cables",
        "sleeve", "pouch", "bag", "bundle", "kit", "accessory", "accessories", "decal",
        "sticker", "stickers", "grip", "dock", "lens", "filter",
    }
)
# "for <something>" is the classic accessory phrasing ("case FOR iphone 15"). Matched as a word.
_FOR_RE = re.compile(r"\bfor\b")

# Stopwords dropped from token signatures (noise that never identifies a product).
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "and", "or", "with", "for", "new", "brand", "genuine",
        "official", "original", "oem", "set", "pack", "x", "in", "to", "by",
    }
)

# A MODEL CODE: an alphanumeric token mixing letters AND digits (``WH-1000XM5`` → ``wh1000xm5``,
# ``RTX3080``, ``A2342``). This is the strong product-identity signal. Separators (-, /, space)
# inside a code are stripped for comparison so ``WH-1000XM5`` == ``wh1000xm5``.
_WORD_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*", re.IGNORECASE)
_HAS_LETTER = re.compile(r"[a-z]", re.IGNORECASE)
_HAS_DIGIT = re.compile(r"\d")


def _to_float(num: str) -> float:
    """Normalize a grouped numeric token (``,``/``.`` as decimal OR thousands) → float (WR-01)."""
    has_dot = "." in num
    has_comma = "," in num
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
    return float(num)


def _strip_diacritics(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize_text(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace. Punctuation handling is left to the
    tokenizer (so model-code separators like ``-`` survive to be stripped there)."""
    text = _strip_diacritics(str(text)).lower()
    return re.sub(r"\s+", " ", text).strip()


def _singularize(tok: str) -> str:
    """Crude, symmetric plural fold: drop a trailing ``s`` on a token longer than 3 chars so
    ``masks`` and ``mask`` compare equal. Applied to BOTH sides so it never skews a comparison."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _is_model_code(tok: str) -> bool:
    core = tok.replace("-", "").replace("/", "")
    return (
        len(core) >= 3
        and bool(_HAS_LETTER.search(core))
        and bool(_HAS_DIGIT.search(core))
    )


def _canon_model(tok: str) -> str:
    return tok.replace("-", "").replace("/", "").lower()


def _tokens_and_models(text: str) -> tuple[list[str], set[str]]:
    """Split normalized ``text`` into ``(significant_tokens, model_codes)``.

    ``significant_tokens`` are singularized, stopword-filtered words (for token-set overlap);
    ``model_codes`` are the canonicalized alphanumeric codes (the strong identity signal)."""
    norm = _normalize_text(text)
    raw = _WORD_RE.findall(norm)
    models: set[str] = set()
    toks: list[str] = []
    for w in raw:
        if _is_model_code(w):
            models.add(_canon_model(w))
            # a model code is ALSO a significant token (its canon form), so a query that is only a
            # model code still has a token to overlap on.
            toks.append(_canon_model(w))
            continue
        w2 = w.replace("-", "").replace("/", "")
        if not w2 or w2 in _STOPWORDS:
            continue
        toks.append(_singularize(w2))
    return toks, models


@dataclass(frozen=True)
class Target:
    """The parsed search target — what the operator is hunting for."""

    raw: str
    brand: str | None
    models: frozenset[str]
    attributes: frozenset[str]
    tokens: frozenset[str]

    @property
    def is_empty(self) -> bool:
        """No usable identity signal → the matcher degrades to match-all (no gating)."""
        return not self.tokens and not self.models


def parse_query(text: str | None) -> Target:
    """Parse a search query / product name into a :class:`Target` (deterministic, per-site-free).

    ``brand`` is a heuristic: the first significant non-model token (most product searches lead
    with the brand — "Sony WH-1000XM5", "Dyson V15"); it refines scoring but is never required.
    ``attributes`` are the non-brand, non-model tokens (size/capacity/color/edition words)."""
    toks, models = _tokens_and_models(text or "")
    brand: str | None = None
    for t in toks:
        if t not in models:
            brand = t
            break
    attributes = frozenset(t for t in toks if t != brand and t not in models)
    return Target(
        raw=str(text or ""),
        brand=brand,
        models=frozenset(models),
        attributes=attributes,
        tokens=frozenset(toks),
    )


@dataclass(frozen=True)
class MatchResult:
    """The verdict for ONE candidate against a target."""

    score: float
    is_match: bool
    is_accessory: bool
    reasons: tuple[str, ...]


def _is_accessory(cand_tokens: list[str], norm_title: str) -> bool:
    if _FOR_RE.search(norm_title):
        return True
    return any(tok in _ACCESSORY_MARKERS for tok in cand_tokens)


def match_candidate(
    target: Target,
    title: str | None,
    *,
    threshold: float | None = None,
) -> MatchResult:
    """Score a candidate ``title`` against ``target`` and decide if it is the SAME product.

    Scoring (0..1), deterministic:

    * **Accessory reject** — a companion marker ("case"/"cover"/"for ..."/"compatible"/...) →
      ``is_match=False`` regardless of score (a "case for WH-1000XM5" is not the headphones).
    * **Model present in the query** — the exact/substring model-code match is the strong signal:
      a candidate that carries the model scores high (``0.6 + 0.4*coverage``); one MISSING the
      model is heavily penalized (``0.3*coverage``) so a different model/variant (XM4 vs XM5) is
      rejected — the EXACT-product contract.
    * **No model in the query** — score is the query-token COVERAGE (fraction of the query's
      tokens the candidate contains), with a small brand-match boost.

    An empty target (no query) matches everything (``score=1.0``) — the matcher only GATES when
    the operator actually searched for something.
    """
    thr = _env_threshold(DEFAULT_MATCH_THRESHOLD if threshold is None else threshold)
    norm_title = _normalize_text(title or "")
    if target.is_empty:
        return MatchResult(1.0, True, False, ("no_query",))
    cand_tokens, cand_models = _tokens_and_models(norm_title)
    reasons: list[str] = []

    accessory = _is_accessory(cand_tokens, norm_title)
    if accessory:
        reasons.append("accessory")

    cand_token_set = set(cand_tokens)
    query_tokens = set(target.tokens)
    coverage = (
        len(query_tokens & cand_token_set) / len(query_tokens) if query_tokens else 1.0
    )

    model_ok: bool | None = None
    if target.models:
        # a target model matches if it (or one of its canon forms) appears as a candidate model
        # code OR as a substring of the joined candidate model codes (e.g. "a2342" in "mkgt3a2342").
        joined = "".join(sorted(cand_models))
        model_ok = any(m in cand_models or m in joined for m in target.models)

    if model_ok is True:
        score = 0.6 + 0.4 * coverage
        reasons.append("model_match")
    elif model_ok is False:
        score = 0.3 * coverage
        reasons.append("model_missing")
    else:
        score = coverage
        reasons.append("token_coverage")

    if target.brand and target.brand in cand_token_set:
        score = min(1.0, score + 0.1)
        reasons.append("brand_match")

    is_match = (not accessory) and score >= thr
    return MatchResult(round(score, 4), is_match, accessory, tuple(reasons))


# --- price + offer ---------------------------------------------------------------------------

_STRIKE_RE = re.compile(r"~~|<del\b|<s>|<strike\b|line-through", re.IGNORECASE)
# Context words that mark a NON-current price (the old / reference / shipping / "from" price) —
# these must never be taken as the product's current price.
_NON_CURRENT_RE = re.compile(
    r"\b(was|rrp|list|were|orig(?:inal)?|save|shipping|delivery|postage|from|starting|msrp)\b",
    re.IGNORECASE,
)


def parse_price_amount(raw: Any) -> tuple[float | None, str | None]:
    """Parse a price string → ``(amount, currency)``; ``(None, None)`` when unparseable.

    A number may be a ``float``/``int`` already (a structured ``offers.price``) — accepted as-is
    with no currency. Mirrors ``db.parse_price`` so the two layers agree."""
    if isinstance(raw, bool):
        return (None, None)
    if isinstance(raw, (int, float)):
        return (float(raw), None)
    if not isinstance(raw, str):
        return (None, None)
    text = raw.strip()
    if not text:
        return (None, None)
    currency: str | None = None
    for sym, code in _CURRENCY_SYMBOLS.items():
        if sym in text:
            currency = code
            break
    if currency is None:
        upper = text.upper()
        for code in _CURRENCY_CODES:
            if re.search(rf"\b{code}\b", upper):
                currency = code
                break
    m = _NUM_RE.search(text)
    if m is None:
        return (None, None)
    try:
        return (_to_float(m.group(0)), currency)
    except ValueError:
        return (None, None)


def normalize_amount(amount: float | None, currency: str | None) -> float | None:
    """Convert ``amount`` in ``currency`` to approximate USD via :data:`STATIC_USD_RATES`.

    Returns ``None`` for a ``None`` amount (a price-less record — it can NEVER win a ranking). An
    unknown/absent currency is assumed already in the common unit (1:1), which is safe because the
    primary ranking groups by currency first; this only orders the cross-currency remainder."""
    if amount is None:
        return None
    rate = STATIC_USD_RATES.get((currency or "").upper(), 1.0)
    return amount * rate


_AVAILABILITY_FALSE = ("outofstock", "out of stock", "soldout", "sold out", "discontinued", "preorder")
_CONDITION_USED = ("used", "refurbished", "renewed", "pre-owned", "preowned", "open box", "openbox")


def _coerce_in_stock(record: Mapping[str, Any]) -> bool | None:
    for key in ("in_stock", "inStock", "available", "availability"):
        if key not in record:
            continue
        val = record[key]
        if isinstance(val, bool):
            return val
        if isinstance(val, str):
            low = _normalize_text(val)
            if any(f in low for f in _AVAILABILITY_FALSE):
                return False
            if "instock" in low.replace(" ", "") or "available" in low:
                return True
    return None


def _coerce_condition(record: Mapping[str, Any]) -> str | None:
    for key in ("condition", "itemCondition"):
        val = record.get(key)
        if isinstance(val, str) and val.strip():
            low = _normalize_text(val)
            return "used" if any(c in low for c in _CONDITION_USED) else "new"
    return None


@dataclass(frozen=True)
class Offer:
    """A comparable candidate offer parsed from a scraped record."""

    title: str
    amount: float | None
    currency: str | None
    in_stock: bool | None
    condition: str | None
    link: str | None
    record: Mapping[str, Any]

    @property
    def has_price(self) -> bool:
        return self.amount is not None

    @property
    def usd(self) -> float | None:
        return normalize_amount(self.amount, self.currency)


def parse_offer(record: Mapping[str, Any]) -> Offer:
    """Read a scraped ``record`` into an :class:`Offer`, PREFERRING a structured price.

    Price source priority (R3): a structured numeric ``price`` / ``offers.price`` /
    ``price_amount`` (JSON-LD / microdata the extractor surfaced) beats a free-text ``price``
    string token. Currency comes from ``priceCurrency`` / ``currency`` when structured, else is
    inferred from the text token. ``in_stock`` / ``condition`` are read when present."""
    title = str(record.get("title") or "")
    amount: float | None = None
    currency: str | None = None

    # 1) a nested structured offer block.
    offers_block = record.get("offers")
    if isinstance(offers_block, Mapping):
        amount, currency = parse_price_amount(offers_block.get("price"))
        cur = offers_block.get("priceCurrency") or offers_block.get("currency")
        if isinstance(cur, str) and cur.strip():
            currency = cur.strip().upper()
    # 2) a flat structured amount.
    if amount is None:
        for key in ("price_amount", "priceAmount", "amount"):
            if key in record:
                amount, currency = parse_price_amount(record.get(key))
                if amount is not None:
                    break
    # 3) an explicit currency field overrides an inferred one.
    cur_field = record.get("priceCurrency") or record.get("currency")
    # 4) the free-text price token (also the source of a symbol-inferred currency).
    if amount is None:
        amount, inferred = parse_price_amount(record.get("price"))
        if currency is None:
            currency = inferred
    if isinstance(cur_field, str) and cur_field.strip():
        currency = cur_field.strip().upper()

    link = record.get("link") or record.get("url")
    return Offer(
        title=title,
        amount=amount,
        currency=currency,
        in_stock=_coerce_in_stock(record),
        condition=_coerce_condition(record),
        link=link if isinstance(link, str) else None,
        record=record,
    )


#: An optional AI re-ranker seam. Given the target and the deterministically-ranked same-product
#: offers, it returns a (possibly re-ordered / filtered) list. Default is ``None`` — the
#: deterministic path is complete without it. An operator-connected brain (the
#: AnthropicAgentProvider) can be adapted to this signature to confirm/refine matches.
MatchReranker = Callable[[Target, "list[Offer]"], "Sequence[Offer]"]


@dataclass(frozen=True)
class RankResult:
    """The outcome of ranking a candidate pool for a target."""

    best: Offer | None
    ranked: tuple[Offer, ...]
    #: same-product offers that cleared the match threshold (the pool ``best`` is drawn from).
    matched: tuple[Offer, ...]
    #: True when NO candidate matched the target and ranking fell back to the full pool.
    fell_back: bool


def _sort_key(prefer_currency: str | None, in_stock_first: bool) -> Callable[[Offer], tuple]:
    pref = (prefer_currency or "").upper()

    def key(o: Offer) -> tuple:
        # 1) priced before price-less (a price-less record can NEVER win).
        no_price = o.amount is None
        # 2) in-stock before unknown before out-of-stock (when requested).
        stock_rank = 0
        if in_stock_first:
            stock_rank = {True: 0, None: 1, False: 2}[o.in_stock]
        # 3) the operator's preferred currency bucket first.
        pref_rank = 0 if (pref and (o.currency or "").upper() == pref) else 1
        # 4) cheapest by USD-normalized amount (currency-safe ordering).
        usd = o.usd
        usd_key = usd if usd is not None else float("inf")
        return (no_price, stock_rank, pref_rank, usd_key)

    return key


def rank_offers(
    target: Target,
    candidates: Sequence[Mapping[str, Any] | Offer],
    *,
    threshold: float | None = None,
    prefer_currency: str | None = None,
    in_stock_first: bool = True,
    rerank: MatchReranker | None = None,
) -> RankResult:
    """Rank candidate records and pick the cheapest offer for the SAME product as ``target``.

    Pipeline: parse each record → keep ONLY same-product candidates (``match_candidate``) → if
    none match, FALL BACK to the full pool (best-effort, so a run never returns nothing — flagged
    ``fell_back=True``) → sort currency-safe (priced-before-priceless, in-stock-first, preferred
    currency, then cheapest USD-normalized). ``best`` is the first ranked offer, and is NEVER a
    price-less record unless EVERY candidate is price-less.

    ``rerank`` (optional, default ``None``) is the AI seam: when supplied it re-orders the matched
    pool AFTER the deterministic sort; the deterministic result stands if it returns nothing
    usable. No API key, no ``rerank`` → fully deterministic.
    """
    offers: list[Offer] = [
        c if isinstance(c, Offer) else parse_offer(c) for c in candidates
    ]
    if not offers:
        return RankResult(None, (), (), False)

    matched = [o for o in offers if match_candidate(target, o.title, threshold=threshold).is_match]
    fell_back = False
    pool = matched
    if not pool:
        pool = offers
        fell_back = True

    ranked = sorted(pool, key=_sort_key(prefer_currency, in_stock_first))

    if rerank is not None:
        try:
            reordered = list(rerank(target, list(ranked)))
        except Exception:  # noqa: BLE001 - an AI re-rank fault must never break the deterministic pick
            reordered = []
        # Only accept a re-rank that keeps a priced offer in front (never let it float a
        # price-less record to the top).
        if reordered and reordered[0].has_price:
            ranked = reordered

    best = ranked[0] if ranked and ranked[0].has_price else None
    if best is None:
        # no priced candidate at all — surface the first record so data is never silently dropped.
        best = ranked[0] if ranked else None
    return RankResult(best, tuple(ranked), tuple(matched), fell_back)


def product_identity_key(title: str | None, fallback: str | None = None) -> str:
    """The stable identity a best-offer row is keyed on (used by ``db._product_key``).

    * model codes present → ``m:`` + the sorted canonical model-code signature, so
      "Sony WH-1000XM5" and "Sony WH-1000XM5 Wireless" collapse to ONE key (same model);
    * else → ``t:`` + the sorted significant-token signature, so the same titled product across
      retailers collapses while two genuinely different token-sets stay distinct;
    * a title with no usable tokens → ``u:`` + ``fallback`` (the page url), the always-available
      last resort (never an empty key)."""
    toks, models = _tokens_and_models(title or "")
    if models:
        return "m:" + "|".join(sorted(models))
    sig = sorted(set(toks))
    if sig:
        return "t:" + " ".join(sig)
    return "u:" + (fallback or "")
