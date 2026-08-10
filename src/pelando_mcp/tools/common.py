"""Shared client and serialisation for the MCP tools."""

from __future__ import annotations

from typing import Any

from ..cache import Cache
from ..client import PelandoClient
from ..models import Deal
from ..normalise import Relevance, looks_like_coupon_code, strip_html, strip_promo_prefixes

_client: PelandoClient | None = None

SCOPE_NOTE = (
    "Pelando is a community deal board, not a price comparator. Prices are numbers users typed "
    "when posting, at the time they posted; they are not verified against the merchant."
)


def get_client() -> PelandoClient:
    global _client
    if _client is None:
        _client = PelandoClient(cache=Cache())
    return _client


def deal_to_dict(deal: Deal, relevance: Relevance | None = None) -> dict[str, Any]:
    """Serialise a deal for the model calling us.

    Field choices worth knowing:
    - `price` may be `null` (a discussion or coupon row) or `0` (a genuinely free game). Both are
      reported as-is; neither means "unknown".
    - `posted_at` is the real ISO timestamp. Pelando's own `date` / `lastActivityAt` are humanised
      pt-BR strings and are passed through separately, clearly labelled.
    - `url` is the merchant's own link. The affiliate redirect is deliberately never surfaced.
    """
    title, tags = strip_promo_prefixes(deal.title)
    out: dict[str, Any] = {
        "id": deal.id,
        "title": deal.title,
        "clean_title": title,
        "tags": tags,
        "kind": deal.kind.value,
        "status": deal.status.value,
        "price_brl": deal.price,
        "temperature": deal.temperature,
        "comment_count": deal.comment_count,
        "store": deal.store.name if deal.store else None,
        "posted_at": deal.first_approved_at or deal.created_at,
        "posted_relative": deal.relative_date,
        "free_shipping": deal.free_shipping,
        "discount_percentage": deal.discount_percentage,
        "url": deal.source_url or None,
        "pelando_url": deal.web_url,
        "note": strip_html(deal.short_description),
    }
    if deal.coupon_code:
        out["coupon_code"] = deal.coupon_code
        if not looks_like_coupon_code(deal.coupon_code):
            out["coupon_code_warning"] = (
                "This does not look like a redeemable code — the field sometimes holds an "
                "instruction rather than a code. Treat it as prose."
            )
    if deal.author is not None:
        out["author"] = {
            "nickname": deal.author.nickname,
            "top_creator": deal.author.top_creator,
        }
    if relevance is not None:
        out["relevance"] = {
            "score": relevance.score,
            "is_match": relevance.is_match,
            "reasons": relevance.reasons,
        }
    return out
