"""Typed calls over the transport in `client.py`.

Two API behaviours make client-side validation mandatory rather than defensive:

- An invalid `kind` returns **HTTP 200 with an empty deals array**, identical to an honest
  "nothing found". A typo is indistinguishable from a real absence.
- An invalid `communitySlug` is **silently ignored** and the global feed comes back instead, so a
  user browsing "tech-lover" with a typo would be shown everything and told it was tech.

Both are validated here, before the request is spent.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog

from .client import TTL, PelandoClient, PelandoError, unwrap
from .models import (
    COMMUNITY_SLUGS,
    MAX_PAGE_SIZE,
    Comment,
    CommentThread,
    Community,
    Deal,
    DealKind,
    DealPage,
    FeedName,
    PageInfo,
    SortOption,
    Store,
)

log = structlog.get_logger("pelando_mcp.api")

MAX_COMMENTS = 200
"""The comments endpoint ignores `limit` and returns the whole tree. We truncate on our side."""


def _clamp_size(size: int) -> int:
    return max(1, min(int(size), MAX_PAGE_SIZE))


def _deals_from(payload: Any) -> list[Deal]:
    out: list[Deal] = []
    for raw in payload.get("deals", []) or []:
        try:
            out.append(Deal.model_validate(raw))
        except Exception as exc:  # a single malformed row must not lose the whole page
            log.warning("deal_parse_failed", deal_id=raw.get("id"), error=str(exc))
    return out


async def search_deals(
    client: PelandoClient,
    term: str,
    *,
    kind: DealKind | None = DealKind.PROMOTION,
    include_expired: bool = False,
    sort: SortOption = SortOption.TEMPERATURE,
    page: int = 1,
    size: int = 20,
) -> DealPage:
    """`GET /feed/search`.

    `page` is 1-indexed and clamped server-side: page ≤ 1 all return the same first records, and
    the offset is `max(0, (page - 1) * size)`.
    """
    size = _clamp_size(size)
    if kind is not None and kind not in set(DealKind):
        raise PelandoError(f"invalid kind {kind!r} — the API would return an empty list silently")

    payload = unwrap(
        await client.get_json(
            "/feed/search",
            {
                "term": term,
                "size": size,
                "page": max(1, int(page)),
                # Defaults to FALSE upstream: expired deals are included unless we opt out, and
                # they dominate any temperature sort because they had time to accumulate heat.
                "hideExpired": not include_expired,
                "kind": kind.value if kind else None,
                "sortOption": SortOption(sort).value,
            },
            ttl=TTL["search"],
        )
    )
    deals = _deals_from(payload)
    dropped = 0
    if kind is DealKind.PROMOTION:
        # Belt and braces. `kind` is undocumented and Pelando's own client never sends it, so if
        # it ever stops being honoured roughly a third of an electronics result set becomes
        # price-null discussion rows. The parameter is an optimisation; this is the guard.
        kept = [d for d in deals if d.price is not None and d.store is not None]
        dropped = len(deals) - len(kept)
        if dropped:
            log.info("dropped_non_offer_rows", count=dropped, term=term)
        deals = kept

    info = PageInfo.model_validate(payload.get("pageInfo") or {})
    return DealPage(
        deals=deals,
        dropped_non_offers=dropped,
        page_info=info,
        # `total` saturates at 10000, and `page` merely echoes the input, so neither can decide
        # this. A full page is the only honest signal that more may exist.
        has_next_page=len(deals) == size,
        total_is_estimate=info.total_is_saturated,
    )


async def browse_feed(
    client: PelandoClient,
    feed: FeedName = FeedName.HOTTEST,
    *,
    community: str | None = None,
    include_expired: bool = False,
    limit: int = 20,
) -> DealPage:
    """`GET /feed/v2/{hottest,recents}`.

    A different envelope from search: `pageInfo` here is cursor-shaped
    (`hasNextPage`, `endCursor`, …) and `hasPreviousPage` is `true` even on the first page, so it
    is ignored.
    """
    if community is not None and community not in COMMUNITY_SLUGS:
        raise PelandoError(
            f"unknown community {community!r}; the API would silently return the global feed. "
            f"Valid slugs: {', '.join(COMMUNITY_SLUGS)}"
        )
    limit = _clamp_size(limit)
    payload = unwrap(
        await client.get_json(
            f"/feed/v2/{FeedName(feed).value}",
            {
                "limit": limit,
                "hideExpired": not include_expired,
                "communitySlug": community,
            },
            ttl=TTL["feed"],
        )
    )
    deals = _deals_from(payload)
    info = payload.get("pageInfo") or {}
    return DealPage(
        deals=deals,
        page_info=PageInfo(page=1, total=len(deals)),
        has_next_page=bool(info.get("hasNextPage")),
    )


async def get_deal(client: PelandoClient, id_or_slug: str) -> Deal:
    """`GET /deals/{uuid|slug}` — one endpoint serves both, so a pasted `/d/<slug>` URL works."""
    payload = unwrap(await client.get_json(f"/deals/{id_or_slug}", ttl=TTL["deal"]))
    return Deal.model_validate(payload)


async def get_comments(
    client: PelandoClient, id_or_slug: str, *, limit: int = 50
) -> CommentThread:
    """`GET /deals/{id}/comments`.

    The endpoint ignores `limit` and `offset` entirely — there is no pagination and no cursor —
    so a heavily commented deal returns its whole tree in one body. Truncation happens here.
    """
    payload = unwrap(await client.get_json(f"/deals/{id_or_slug}/comments", ttl=TTL["comments"]))
    raw = payload.get("comments") or []
    comments = [Comment.model_validate(c) for c in raw[: max(1, limit)]]
    return CommentThread(
        totalComments=payload.get("totalComments", 0),
        comments=comments,
        truncated=len(raw) > len(comments),
    )


async def search_stores(client: PelandoClient, term: str) -> list[Store]:
    """`GET /stores/search` — matches loosely on substrings ("amazon" also returns "Amaro"), so
    never trust the first result blindly. This is the only shape that populates `coupons`."""
    payload = unwrap(await client.get_json("/stores/search", {"term": term}, ttl=TTL["stores"]))
    return [Store.model_validate(s) for s in payload.get("stores", []) or []]


async def all_stores(client: PelandoClient) -> list[Store]:
    """`GET /stores/all` — every merchant in one ~300 KB response. Cached hard; this is the join
    table that turns a deal's bare `store.logo` storage key into a usable signed URL."""
    payload = unwrap(await client.get_json("/stores/all", ttl=TTL["stores"]))
    return [Store.model_validate(s) for s in payload.get("stores", []) or []]


async def get_store(client: PelandoClient, slug: str) -> Store:
    """`GET /stores/{slug}` — the richest shape, but it drops `coupons`."""
    payload = unwrap(await client.get_json(f"/stores/{slug}", ttl=TTL["stores"]))
    return Store.model_validate(payload)


async def list_communities(client: PelandoClient) -> list[Community]:
    """`GET /communities`. Falls back to the hardcoded slugs if the endpoint ever moves —
    the taxonomy has exactly 11 entries and there is no category system beside it."""
    try:
        payload = unwrap(await client.get_json("/communities", ttl=TTL["communities"]))
        found = [Community.model_validate(c) for c in payload.get("communities", []) or []]
        if found:
            return found
    except PelandoError as exc:
        log.warning("communities_endpoint_failed", error=str(exc))
    return [Community(slug=s) for s in COMMUNITY_SLUGS]


def coupons_of(stores: Iterable[Store]) -> list[Any]:
    out: list[Any] = []
    for store in stores:
        out.extend(store.coupons)
    return out
