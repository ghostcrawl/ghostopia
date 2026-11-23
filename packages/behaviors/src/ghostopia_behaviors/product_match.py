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
   (tunable: arg ``threshold``/env ``GHOSTOPIA_MATCH_THRESHOLD``/default
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
supply to re-order/confirm the same-product set. It defaults to ``None`` — the deterministic
path works fully with NO API key.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ghostopia_shared.price import STATIC_USD_RATES, normalize_amount
from ghostopia_shared.price import parse_price_amount as _shared_parse_price_amount

__all__ = [
    "DEFAULT_MATCH_THRESHOLD",
    "DEFAULT_TOKEN_THRESHOLD",
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

# The default same-product acceptance threshold (0..1) on the MODEL path (the query carries a
# model code): a candidate scores ABOVE this to count as "the same product". Override per call
# (``threshold=``) or globally via ``GHOSTOPIA_MATCH_THRESHOLD`` (an operator knob, no redeploy).
DEFAULT_MATCH_THRESHOLD = 0.5
# The default on the TOKEN path (no model code in the query): the candidate must carry at least
# this fraction of the query's significant tokens. 0.8 = every token of a <=4-token query, 4 of 5
# for longer ones (/ : a 50% overlap merged "Kindle Paperwhite" with "Kindle Oasis").
DEFAULT_TOKEN_THRESHOLD = 0.8


def _env_threshold(fallback: float) -> float:
    raw = os.environ.get("GHOSTOPIA_MATCH_THRESHOLD")
    if not raw:
        return fallback
    try:
        val = float(raw)
    except ValueError:
        return fallback
    return val if 0.0 <= val <= 1.0 else fallback


# --- currency + price: ONE shared parser -------------------------------------------
# The rate table, currency detection and the price parser live in ``ghostopia_shared.price`` and
# are re-exported here so existing callers keep working. Ranking is CURRENCY-SAFE: amounts are
# ordered on an approximate USD value; an unknown currency is NOT assumed to be USD — it ranks
# after every known-currency offer (it never wins a cross-currency comparison).

# Accessory/companion markers. A HARD marker in the product part of a title (before " with ")
# makes the listing a companion item unless the operator searched for that accessory.
_ACCESSORY_MARKERS: frozenset[str] = frozenset(
    {
        "case", "cover", "skin", "protector", "screenprotector", "strap", "replacement",
        "compatible", "adapter", "adaptor", "charger", "cable", "sleeve", "pouch", "bag",
        "accessory", "accessorie", "decal", "sticker", "grip", "dock", "mount", "holder",
        "pad", "earpad", "cushion", "tip", "eartip", "refill", "battery",
    }
)
# SOFT markers: words that often ride on the real product ("camera with lens kit", "console
# bundle", "watch with sport band", "water filter"). They down-weight, never hard-reject.
_SOFT_MARKERS: frozenset[str] = frozenset({"kit", "bundle", "lens", "filter", "band", "stand"})
_FOR_RE = re.compile(r"\bfor\b")
_WITH_SPLIT_RE = re.compile(r"\s(?:with|w/|incl(?:\.|uding)?|\+\s)\s")

# Noise that never identifies a product (incl. shopping intent words in a query).
_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "a", "an", "of", "and", "or", "with", "for", "new", "brand", "genuine",
        "official", "original", "oem", "set", "in", "to", "by", "generation", "gen",
        "best", "price", "cheap", "cheapest", "deal", "deals", "buy", "sale", "discount", "online",
    }
)
# VARIANT words distinguish otherwise-identical model lines (iPhone 15 vs 15 Pro Max, Switch
# OLED vs Lite, vs FE/+). The query and candidate must carry the SAME set.
_VARIANTS: frozenset[str] = frozenset(
    {
        "pro", "max", "plus", "ultra", "lite", "mini", "air", "se", "oled", "fe", "xl", "slim",
        "ti", "super", "neo", "prime",
    }
)
# Spec/unit tokens: capacity, power, size, network, resolution. They are NEVER a model
# identity (``128gb``/``65w``/``5g``/``4k`` used to merge unrelated products —); a
# DIFFERENT value of the same unit is a hard conflict (128GB vs 256GB).
_SPEC_RE = re.compile(
    r"^(\d+(?:\.\d+)?)(gb|tb|mb|kb|w|kw|mah|wh|hz|khz|mhz|ghz|k|p|mm|cm|inch|ft|v|g|kg|lb|lbs|oz|"
    r"ml|qt|quart|mp|fps|nm|x|pc|pcs|ct|pack|count|core|cores|ah|db)$"
)
_SPEC_PREFIX_RE = re.compile(r"^(usb|ddr|pcie|lte|hdmi|wifi|ipx|ip|bt|gen)(\d+(?:\.\d+)?)$")
_UNIT_ALIAS = {"lbs": "lb", "quart": "qt", "pcs": "pc", "cores": "core"}
_WORD_RE = re.compile(r"[a-z0-9]+(?:[-/][a-z0-9]+)*", re.IGNORECASE)
_HAS_LETTER = re.compile(r"[a-z]", re.IGNORECASE)
_HAS_DIGIT = re.compile(r"\d")
_UNIT_JOIN_RE = re.compile(
    r"\b(\d+(?:\.\d+)?)\s+(gb|tb|mb|w|mah|hz|ghz|mm|cm|inch|inches|v|mp|oz|ml|qt|quart|lbs?|kg)\b"
)
_ORDINAL_RE = re.compile(r"\b(\d+)(?:st|nd|rd|th)\b")
_MARK_RE = re.compile(r"\b(?:mark|mk)\s*(ii|iii|iv|v|vi|[2-9])\b")
_ROMAN = {"ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}


def _strip_diacritics(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def _normalize_text(text: str) -> str:
    """Lowercase, strip diacritics, canonicalize ``+``/``w/``/ordinals/``Mark II``/``128 GB``,
    collapse whitespace."""
    text = _strip_diacritics(str(text)).lower()
    text = text.replace("–", " ").replace("—", " ")
    text = re.sub(r"\bw/\s*", " with ", text)
    text = re.sub(r"([a-z0-9])\+", r"\1 plus", text)
    text = _MARK_RE.sub(lambda m: " mark" + _ROMAN.get(m.group(1), m.group(1)) + " ", text)
    text = _ORDINAL_RE.sub(r"\1", text)
    text = _UNIT_JOIN_RE.sub(lambda m: m.group(1) + ("inch" if m.group(2).startswith("inch") else m.group(2)), text)
    text = re.sub(r"(\d)\s*(?:\"|”|''|-inch\b)", r"\1inch ", text)
    return re.sub(r"\s+", " ", text).strip()


def _singularize(tok: str) -> str:
    """Crude, symmetric plural fold (``masks``→``mask``), applied to both sides."""
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _canon_model(tok: str) -> str:
    # ``WH1000XM5/B``/``MKGT3LL/A`` → drop a trailing short colour/region suffix after ``/``.
    if "/" in tok:
        head, tail = tok.rsplit("/", 1)
        if tail.isalpha() and len(tail) <= 3:
            tok = head
    return tok.replace("-", "").replace("/", "").lower()


def _spec(tok: str) -> tuple[str, str] | None:
    """``(unit, value)`` when ``tok`` is a spec/unit token (``128gb`` → ``('gb','128')``)."""
    m = _SPEC_RE.match(tok)
    if m:
        unit = _UNIT_ALIAS.get(m.group(2), m.group(2))
        return unit, m.group(1)
    m = _SPEC_PREFIX_RE.match(tok)
    if m:
        return m.group(1), m.group(2)
    return None


@dataclass(frozen=True)
class _Parsed:
    tokens: list[str]
    models: set[str]
    numbers: set[str]
    variants: set[str]
    specs: set[tuple[str, str]]


def _parse_text(norm: str) -> _Parsed:
    """Classify the tokens of NORMALIZED text (see :func:`_normalize_text`)."""
    toks: list[str] = []
    models: set[str] = set()
    numbers: set[str] = set()
    variants: set[str] = set()
    specs: set[tuple[str, str]] = set()
    for w in _WORD_RE.findall(norm):
        core = _canon_model(w)
        if not core:
            continue
        sp = _spec(core)
        if sp is not None:
            specs.add(sp)
            toks.append(core)
            continue
        if core.isdigit():
            numbers.add(core.lstrip("0") or "0")
            toks.append(core)
            continue
        if _HAS_LETTER.search(core) and _HAS_DIGIT.search(core):
            if re.fullmatch(r"mark\d", core):
                variants.add(core)
            else:
                models.add(core)
            toks.append(core)
            continue
        w2 = core if core in _VARIANTS else _singularize(core)
        if w2 in _STOPWORDS:
            continue
        if w2 in _VARIANTS:
            variants.add(w2)
        toks.append(w2)
    return _Parsed(toks, models, numbers, variants, specs)


def _tokens_and_models(text: str) -> tuple[list[str], set[str]]:
    """Back-compat helper: ``(significant_tokens, model_codes)`` of ``text``."""
    p = _parse_text(_normalize_text(text))
    return p.tokens, p.models


@dataclass(frozen=True)
class Target:
    """The parsed search target — what the operator is hunting for."""

    raw: str
    brand: str | None
    models: frozenset[str]
    attributes: frozenset[str]
    tokens: frozenset[str]
    numbers: frozenset[str] = frozenset()
    variants: frozenset[str] = frozenset()
    specs: frozenset[tuple[str, str]] = frozenset()
    #: accessory markers the operator searched for ("iPhone 15 case" → {"case"}).
    accessories: frozenset[str] = frozenset()

    @property
    def is_empty(self) -> bool:
        """No usable identity signal → the matcher degrades to match-all (no gating)."""
        return not self.tokens and not self.models


def parse_query(text: str | None) -> Target:
    """Parse a search query/product name into a :class:`Target` (deterministic, per-site-free).

    ``brand`` = the first plain token (not a model/number/spec/variant). ``models`` are true
    model identifiers (letters+digits, never a unit/spec token); ``numbers``/``variants`` /
    ``specs`` are the generation, line and capacity signals that must agree with a candidate."""
    p = _parse_text(_normalize_text(text or ""))
    spec_toks = {f"{v}{u}" for u, v in p.specs}
    plain = [t for t in p.tokens if t not in p.models and t not in p.numbers and t not in spec_toks]
    brand = next((t for t in plain if t not in p.variants), None)
    attributes = frozenset(t for t in p.tokens if t != brand and t not in p.models)
    return Target(
        raw=str(text or ""),
        brand=brand,
        models=frozenset(p.models),
        attributes=attributes,
        tokens=frozenset(p.tokens),
        numbers=frozenset(p.numbers),
        variants=frozenset(p.variants),
        specs=frozenset(p.specs),
        accessories=frozenset(t for t in p.tokens if t in _ACCESSORY_MARKERS),
    )


@dataclass(frozen=True)
class MatchResult:
    """The verdict for ONE candidate against a target."""

    score: float
    is_match: bool
    is_accessory: bool
    reasons: tuple[str, ...]


def _product_segment(norm_title: str, target: Target) -> tuple[str, bool]:
    """``(segment, accessory_for)`` — the part of the title that names THE product.

    "Case for iPhone 15 Pro" names an accessory FOR a product: when the operator searched that
    accessory, the identity checks run on the part after "for"; otherwise the part before
    " with " (bundled contents — "with Sport Band", "with Lens Kit" — never decide identity)."""
    head = _WITH_SPLIT_RE.split(f" {norm_title} ", maxsplit=1)[0].strip()
    fors = list(_FOR_RE.finditer(norm_title))
    m = fors[-1] if fors else None
    if m is not None:
        after = norm_title[m.end():].strip()
        after_toks = set(_parse_text(after).tokens)
        if after_toks & (set(target.tokens) - set(target.accessories)):
            return (after if target.accessories else head), True
    return head, False


def match_candidate(
    target: Target,
    title: str | None,
    *,
    threshold: float | None = None,
) -> MatchResult:
    """Decide whether ``title`` names the SAME product as ``target`` (score 0..1 + reasons).

    Hard rejects (``is_match=False`` whatever the score):

    * **accessory** — a hard companion marker the operator did not search ("case", "cover",
      "replacement", "compatible", ...), or "X for <the product>"; an accessory QUERY instead
      requires the candidate to carry the searched marker;
    * **model_missing** — EVERY query model code must appear exactly (no substring merging);
    * **variant_mismatch** — the variant set (pro/max/plus/ultra/lite/mini/air/oled/fe/mark N...)
      must be identical;
    * **number_missing** — every bare query number (generation/series: "15", "5", "2") present;
    * **spec_conflict** — a candidate stating a different value of a query unit (128GB vs 256GB).

    Score: model path ``0.6 + 0.4*coverage`` (+0.1 brand), token path = query-token coverage;
    a SOFT marker ("kit"/"bundle"/"lens"/...) the query lacks costs 0.1. An empty target matches
    everything (``score=1.0``)."""
    norm_title = _normalize_text(title or "")
    if target.is_empty:
        return MatchResult(1.0, True, False, ("no_query",))
    whole = _parse_text(norm_title)
    segment, accessory_for = _product_segment(norm_title, target)
    seg = _parse_text(segment) if segment != norm_title else whole
    head = _parse_text(_WITH_SPLIT_RE.split(f" {norm_title} ", maxsplit=1)[0].strip())
    reasons: list[str] = []
    hard: list[str] = []

    cand_tokens = set(whole.tokens)
    # --- accessory ---------------------------------------------------------------------------
    accessory = False
    if target.accessories:
        if not target.accessories <= cand_tokens:
            hard.append("accessory_missing")
    else:
        if accessory_for or any(t in _ACCESSORY_MARKERS for t in head.tokens):
            accessory = True
    if accessory:
        reasons.append("accessory")

    # --- identity checks on the product segment ----------------------------------------------
    model_ok: bool | None = None
    if target.models:
        model_ok = target.models <= seg.models
        if not model_ok:
            hard.append("model_missing")
    if target.variants != seg.variants:
        hard.append("variant_mismatch")
    if not target.numbers <= seg.numbers:
        hard.append("number_missing")
    cand_units: dict[str, set[str]] = {}
    for u, v in whole.specs:
        cand_units.setdefault(u, set()).add(v)
    for u, v in target.specs:
        vals = cand_units.get(u)
        if vals and v not in vals:
            hard.append("spec_conflict")
            break

    query_tokens = set(target.tokens)
    coverage = len(query_tokens & cand_tokens) / len(query_tokens) if query_tokens else 1.0
    if model_ok is True:
        score = 0.6 + 0.4 * coverage
        reasons.append("model_match")
        default_thr = DEFAULT_MATCH_THRESHOLD
        if target.brand and target.brand in cand_tokens:
            score = min(1.0, score + 0.1)
            reasons.append("brand_match")
    elif model_ok is False:
        score = 0.3 * coverage
        reasons.append("model_missing")
        default_thr = DEFAULT_MATCH_THRESHOLD
    else:
        score = coverage
        reasons.append("token_coverage")
        default_thr = DEFAULT_TOKEN_THRESHOLD
    soft = [t for t in whole.tokens if t in _SOFT_MARKERS and t not in query_tokens]
    if soft:
        score = max(0.0, score - 0.1)
        reasons.append("soft_accessory_marker")
    reasons.extend(r for r in hard if r not in reasons)
    thr = _env_threshold(default_thr if threshold is None else threshold)

    is_match = (not accessory) and not hard and score >= thr
    return MatchResult(round(score, 4), is_match, accessory, tuple(reasons))


# --- price + offer ---------------------------------------------------------------------------


def parse_price_amount(raw: Any) -> tuple[float | None, str | None]:
    """``(amount, currency)`` via the ONE shared parser (``ghostopia_shared.price``).

    ``amount`` is ``None`` for an unusable value: unparseable, ``<= 0``/Free, multi-buy,
    instalment or a non-current ("Was"/"RRP"/struck) price."""
    return _shared_parse_price_amount(raw)


_AVAILABILITY_FALSE = ("outofstock", "out of stock", "soldout", "sold out", "discontinued")
_CONDITION_USED = ("used", "refurbished", "renewed", "pre-owned", "preowned", "open box", "openbox", "damaged")


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

    @property
    def eligible(self) -> bool:
        """Can this offer be THE best? Priced (> 0), not out of stock, not used/refurbished."""
        return self.has_price and self.in_stock is not False and self.condition in (None, "new")


def parse_offer(record: Mapping[str, Any]) -> Offer:
    """Read a scraped ``record`` into an :class:`Offer`, PREFERRING a structured price.

    Priority: a nested ``offers`` block, then a flat structured amount (``price_num`` /
    ``price_amount`` ...), then the free-text ``price`` token. An explicit ``priceCurrency`` /
    ``currency`` field beats a symbol-inferred currency."""
    title = str(record.get("title") or "")
    amount: float | None = None
    currency: str | None = None

    offers_block = record.get("offers")
    if isinstance(offers_block, Mapping):
        cur = offers_block.get("priceCurrency") or offers_block.get("currency")
        amount, currency = _shared_parse_price_amount(
            offers_block.get("price"), currency_hint=cur if isinstance(cur, str) else None
        )
    if amount is None:
        for key in ("price_num", "price_amount", "priceAmount", "amount"):
            if key in record:
                amount, currency = _shared_parse_price_amount(record.get(key))
                if amount is not None:
                    break
    cur_field = record.get("priceCurrency") or record.get("currency")
    if amount is None:
        amount, inferred = _shared_parse_price_amount(record.get("price"))
        if currency is None:
            currency = inferred
    elif currency is None and isinstance(record.get("price"), str):
        currency = _shared_parse_price_amount(record.get("price"))[1]
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
#: offers, it returns a (possibly re-ordered/filtered) list. Default is ``None`` — the
#: deterministic path is complete without it. An operator-connected brain (the
#: AnthropicAgentProvider) can be adapted to this signature to confirm/refine matches.
MatchReranker = Callable[[Target, "list[Offer]"], "Sequence[Offer]"]


@dataclass(frozen=True)
class RankResult:
    """The outcome of ranking a candidate pool for a target."""

    #: the cheapest ELIGIBLE same-product offer, or ``None`` (never an unrelated fallback).
    best: Offer | None
    #: the ranked pool: the matched offers, or — when nothing matched — the full pool as
    #: RELATED candidates only (``fell_back=True``). ``ranked[0]`` is never "best" by itself.
    ranked: tuple[Offer, ...]
    #: same-product offers that cleared the match threshold (the pool ``best`` is drawn from).
    matched: tuple[Offer, ...]
    #: True when NO candidate matched the target (``best`` is then ``None``).
    fell_back: bool
    #: ``matched`` | ``no_exact_match`` | ``no_eligible_offer`` | ``no_candidates``.
    status: str = "matched"


def _sort_key(
    prefer_currency: str | None, in_stock_first: bool, any_known_currency: bool
) -> Callable[[Offer], tuple]:
    pref = (prefer_currency or "").upper()

    def key(o: Offer) -> tuple:
        no_price = o.amount is None
        stock_rank = {True: 0, None: 1, False: 2}[o.in_stock] if in_stock_first else 0
        cond_rank = 0 if o.condition in (None, "new") else 1
        pref_rank = 0 if (pref and (o.currency or "").upper() == pref) else 1
        usd = o.usd
        # An unknown currency never wins a cross-currency comparison .
        unknown = 1 if (usd is None and any_known_currency) else 0
        val = usd if usd is not None else (o.amount if o.amount is not None else float("inf"))
        return (no_price, stock_rank, cond_rank, pref_rank, unknown, val)

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

    Pipeline: parse → keep ONLY same-product candidates (:func:`match_candidate`) → sort
    currency-safe (priced first, in-stock first, new first, preferred currency, cheapest USD).
    ``best`` is the first ELIGIBLE offer (priced > 0, not out of stock, not used).

    When nothing matches there is NO best : ``best=None``, ``status="no_exact_match"``,
    ``fell_back=True`` and ``ranked`` holds the full pool as RELATED candidates for display only.

    ``rerank`` (optional) is the AI seam: it may re-order the matched pool; it is ignored when it
    faults or puts an ineligible offer first."""
    offers: list[Offer] = [c if isinstance(c, Offer) else parse_offer(c) for c in candidates]
    if not offers:
        return RankResult(None, (), (), False, "no_candidates")

    matched = [o for o in offers if match_candidate(target, o.title, threshold=threshold).is_match]
    known = any(o.usd is not None for o in offers)
    if not matched:
        related = sorted(offers, key=_sort_key(prefer_currency, in_stock_first, known))
        return RankResult(None, tuple(related), (), True, "no_exact_match")

    ranked = sorted(matched, key=_sort_key(prefer_currency, in_stock_first, known))
    if rerank is not None:
        try:
            reordered = list(rerank(target, list(ranked)))
        except Exception:  # noqa: BLE001 - an AI re-rank fault must never break the deterministic pick
            reordered = []
        if reordered and reordered[0].eligible:
            ranked = reordered

    best = next((o for o in ranked if o.eligible), None)
    return RankResult(best, tuple(ranked), tuple(matched), False,
                      "matched" if best is not None else "no_eligible_offer")


def _gtin14(raw: Any) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return digits.zfill(14) if 8 <= len(digits) <= 14 else None


def product_identity_key(
    title: str | None,
    fallback: str | None = None,
    *,
    gtin: str | None = None,
    mpn: str | None = None,
    brand: str | None = None,
) -> str:
    """The stable identity an offer row is keyed on.

    Priority : ``g:`` GTIN (zero-padded to 14) > ``p:`` brand+MPN > ``m:`` model codes
    + variant + spec tokens (``m:256gb|s24`` ≠ ``m:256gb|s23``) > ``t:`` sorted token signature
    (unit tokens like ``128gb``/``65w`` are part of it, never the whole identity) > ``u:`` url."""
    g = _gtin14(gtin)
    if g:
        return "g:" + g
    if isinstance(mpn, str) and mpn.strip():
        b = _normalize_text(brand) if isinstance(brand, str) else ""
        return "p:" + b + "|" + _canon_model(_normalize_text(mpn).replace(" ", ""))
    norm = _normalize_text(title or "")
    head = _parse_text(_WITH_SPLIT_RE.split(f" {norm} ", maxsplit=1)[0].strip())
    # An accessory FOR a product ("WH-1000XM5 Ear Pads Replacement") must never share the
    # product's key, or it competes for the product's best price (M1).
    acc = "|acc" if (
        any(t in _ACCESSORY_MARKERS for t in head.tokens) or _FOR_RE.search(norm)
    ) else ""
    if head.models:
        parts = set(head.models) | set(head.variants) | {f"{v}{u}" for u, v in head.specs}
        return "m:" + "|".join(sorted(parts)) + acc
    sig = sorted(set(head.tokens))
    if sig:
        return "t:" + " ".join(sig) + acc
    return "u:" + (fallback or "")
