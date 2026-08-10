"""Cleaning helpers for free-text community deal titles.

Pelando titles are typed by users. There is no brand, model, capacity, SKU, EAN or condition field
anywhere in the payload — only a string like:

    [ PRIME ] Apple iPhone 16 (512 GB) – Preto
    [REEMBALADO] GeForce RTX 5070 OC 12GB
    (MELI 8667,00) Apple iPhone 16 Pro Max
    [Com Cashback Pelando R$10439.10] Apple iPhone 16 Pro

This module stays deliberately small. It does two jobs the server would be wrong without —
never presenting an open-box price as a new-unit price, and not returning phone cases for a phone
query — and it does not attempt to build a product catalogue. See PLAN.md §2 and §5.

Search on Pelando is token-based and loose: `iphone 16 pro` matches "iPhone 14 Pro 128GB IOS 16"
because every token appears somewhere. Relevance filtering is therefore not optional polish.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from selectolax.parser import HTMLParser

from .models import Condition

# Leading "[ PRIME ]", "(MELI 8667,00)", "[Moedas R$22]" blocks. Repeated, in either bracket style.
_PREFIX_RE = re.compile(r"^\s*(?:\[([^\]]*)\]|\(([^)]*)\))\s*")

_CONDITION_PATTERNS: tuple[tuple[Condition, re.Pattern[str]], ...] = (
    (Condition.REEMBALADO, re.compile(r"\breembalad[oa]s?\b")),
    (Condition.OPEN_BOX, re.compile(r"\bopen\s*box\b|\bvitrine\b|\bmostruario\b")),
    (Condition.REFURBISHED, re.compile(r"\brecondicionad[oa]s?\b|\brefurbished\b|\brefurb\b")),
    (Condition.USED, re.compile(r"\busad[oa]s?\b|\bseminov[oa]s?\b|\bsegunda\s+mao\b")),
)

# Words that make a result an accessory FOR something rather than the something. Only ever applied
# when the query itself does not mention them.
_ACCESSORY_TOKENS = frozenset(
    {
        "capa",
        "capinha",
        "case",
        "cabo",
        "carregador",
        "pelicula",
        "protetor",
        "suporte",
        "adaptador",
        "dock",
        "base",
        "bateria",
        "fonte",
        "mochila",
        "bolsa",
        "kit",
        "caneta",
        "cartao",
        "memoria",
    }
)

# Tokens that distinguish variants: "RTX 5070" must not match "RTX 5070 Ti", nor "iPhone 16" the
# "iPhone 16 Pro Max".
_VARIANT_TOKENS = frozenset({"ti", "pro", "max", "plus", "ultra", "super", "mini", "lite", "air"})

_STOPWORDS = frozenset({"de", "da", "do", "com", "sem", "para", "e", "a", "o", "em", "no", "na"})

_CAPACITY_RE = re.compile(r"\b(\d+)\s*(gb|tb|mb)\b")
_SCREEN_RE = re.compile(r'\b(\d{2,3})\s*(?:"|polegadas|pol)\b')

_COUPON_CODE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,31}$")


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def fold(text: str) -> str:
    """Lowercase, accent-stripped form used for all matching."""
    return strip_accents(text).lower()


def strip_html(text: str | None) -> str | None:
    """`shortDescription`, store `description` and coupon `rulesDescription` arrive as raw HTML.

    Community descriptions are plain text — do not run those through here.
    """
    if text is None:
        return None
    if "<" not in text:
        return text.strip() or None
    cleaned = HTMLParser(text).text(separator=" ").strip()
    return re.sub(r"\s+", " ", cleaned) or None


def strip_promo_prefixes(title: str) -> tuple[str, list[str]]:
    """Pull leading bracket/paren blocks off a title and return them as tags.

    These carry real information (PRIME, MELI, cashback amounts) but they are not part of the
    product name, and leaving them in wrecks token matching.
    """
    tags: list[str] = []
    remaining = title
    while True:
        match = _PREFIX_RE.match(remaining)
        if not match:
            break
        tag = (match.group(1) or match.group(2) or "").strip()
        if tag:
            tags.append(tag)
        remaining = remaining[match.end() :]
    return remaining.strip() or title.strip(), tags


def detect_condition(title: str) -> Condition:
    """Detect a non-new condition from the title.

    Defaults to `UNKNOWN`, never to `NEW`. A silent title is not evidence that a unit is new, and
    reporting an open-box price as a new-unit price is the single most misleading thing this
    server could do.
    """
    folded = fold(title)
    for condition, pattern in _CONDITION_PATTERNS:
        if pattern.search(folded):
            return condition
    return Condition.UNKNOWN


def looks_like_coupon_code(value: str | None) -> bool:
    """Guard against presenting prose as a redeemable code.

    Observed in the wild: `couponCode` holding the literal string
    "Resgatar cupom abaixo do produto".
    """
    if not value:
        return False
    return bool(_COUPON_CODE_RE.match(value.strip()))


def tokenise(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", fold(text)) if t and t not in _STOPWORDS]


@dataclass(slots=True)
class Relevance:
    """Why a result was kept, demoted, or rejected."""

    score: float
    is_match: bool
    reasons: list[str] = field(default_factory=list)


def assess_relevance(query: str, title: str) -> Relevance:
    """Score a deal title against the user's query.

    Deliberately conservative in one direction only: a near-miss is *demoted* and returned as
    related, never silently dropped. The caller decides what to show.
    """
    clean_title, _tags = strip_promo_prefixes(title)
    q_tokens = tokenise(query)
    t_tokens = set(tokenise(clean_title))
    if not q_tokens:
        return Relevance(score=0.0, is_match=False, reasons=["empty query"])

    hits = [t for t in q_tokens if t in t_tokens]
    score = len(hits) / len(q_tokens)
    reasons: list[str] = []

    missing = [t for t in q_tokens if t not in t_tokens]
    if missing:
        reasons.append(f"query tokens absent from title: {', '.join(missing)}")

    # Capacity must agree when the query states one — "256GB" and "512GB" are different products.
    q_caps = {f"{n}{u}" for n, u in _CAPACITY_RE.findall(fold(query))}
    t_caps = {f"{n}{u}" for n, u in _CAPACITY_RE.findall(fold(clean_title))}
    if q_caps and t_caps and not (q_caps & t_caps):
        score *= 0.4
        reasons.append(f"capacity mismatch: wanted {'/'.join(sorted(q_caps))}")

    q_screen = set(_SCREEN_RE.findall(fold(query)))
    t_screen = set(_SCREEN_RE.findall(fold(clean_title)))
    if q_screen and t_screen and not (q_screen & t_screen):
        score *= 0.5
        reasons.append("screen size mismatch")

    # A variant token the query did not ask for means a different SKU (RTX 5070 vs 5070 Ti).
    # Penalised below the match threshold on purpose: a different SKU is a harder miss than a
    # partial token match, so "RTX 5070 Ti" must never be returned as a match for "RTX 5070".
    extra_variants = (t_tokens & _VARIANT_TOKENS) - set(q_tokens)
    if extra_variants:
        score *= 0.5
        reasons.append(f"different variant: {', '.join(sorted(extra_variants))}")

    # An accessory word the query never mentioned: a case is not a phone.
    accessories = (t_tokens & _ACCESSORY_TOKENS) - set(q_tokens)
    if accessories:
        score *= 0.3
        reasons.append(f"looks like an accessory: {', '.join(sorted(accessories))}")

    return Relevance(score=round(score, 3), is_match=score >= 0.6, reasons=reasons)
