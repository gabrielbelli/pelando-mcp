"""Deal search, browsing, and the crowd-verdict tool."""

from __future__ import annotations

from typing import Any, Literal

from .. import api
from ..models import DealKind, FeedName, SortOption
from ..normalise import assess_relevance
from ..quality import assess
from .common import SCOPE_NOTE, deal_to_dict, get_client


def register_deal_tools(mcp: Any) -> None:
    @mcp.tool()
    async def search_deals(
        query: str,
        kind: Literal["promotion", "discussion", "coupon"] = "promotion",
        include_expired: bool = False,
        sort: Literal["temperature", "createdAt", "lastCommentedAt"] = "temperature",
        page: int = 1,
        size: int = 20,
        min_temperature: int | None = None,
        max_price: float | None = None,
        free_shipping_only: bool = False,
        store: str | None = None,
        drop_irrelevant: bool = True,
    ) -> dict:
        """Search deals that Pelando users have posted.

        Returns community postings, NOT a catalogue: one merchant per posting, free-text titles,
        and only products someone chose to post. There is no product database behind this — even
        mainstream terms can legitimately return nothing.

        Pelando's search is token-based and loose ("iphone 16 pro" matches "iPhone 14 Pro ... 16"),
        so each result carries a `relevance` block. With `drop_irrelevant` the weak matches are
        moved to `related` rather than deleted, so nothing is hidden from you.

        Only `include_expired` and `sort` are server-side. Every other filter is applied locally
        after fetching, so a narrow filter with a small `size` may return few rows even when more
        exist upstream.
        """
        client = get_client()
        page_result = await api.search_deals(
            client,
            query,
            kind=DealKind(kind),
            include_expired=include_expired,
            sort=SortOption(sort),
            page=page,
            size=size,
        )

        matches: list[dict] = []
        related: list[dict] = []
        for deal in page_result.deals:
            if min_temperature is not None and deal.temperature < min_temperature:
                continue
            if max_price is not None and (deal.price is None or deal.price > max_price):
                continue
            if free_shipping_only and not deal.free_shipping:
                continue
            if store and (deal.store is None or store.lower() not in deal.store.name.lower()):
                continue
            relevance = assess_relevance(query, deal.title)
            record = deal_to_dict(deal, relevance)
            if relevance.is_match or not drop_irrelevant:
                matches.append(record)
            else:
                related.append(record)

        return {
            "query": query,
            "deals": matches,
            "related": related,
            "returned": len(matches),
            "has_next_page": page_result.has_next_page,
            "upstream_total": (
                "10000+ (the API saturates this counter; it is not a match count)"
                if page_result.total_is_estimate
                else page_result.page_info.total
            ),
            "note": SCOPE_NOTE,
        }

    @mcp.tool()
    async def browse_feed(
        feed: Literal["hottest", "recents"] = "hottest",
        community: str | None = None,
        include_expired: bool = False,
        limit: int = 20,
    ) -> dict:
        """Browse Pelando's own feeds, optionally scoped to one community.

        `community` must be one of the 11 canonical slugs — there is no finer taxonomy on the site.
        For electronics use `tech-lover` or `mundo-gamer`. An unknown slug is rejected here rather
        than sent, because the API silently ignores it and returns the global feed instead.
        """
        client = get_client()
        result = await api.browse_feed(
            client,
            FeedName(feed),
            community=community,
            include_expired=include_expired,
            limit=limit,
        )
        return {
            "feed": feed,
            "community": community,
            "deals": [deal_to_dict(d) for d in result.deals],
            "has_next_page": result.has_next_page,
            "note": SCOPE_NOTE,
        }

    @mcp.tool()
    async def get_deal(id_or_slug: str) -> dict:
        """Fetch one deal by UUID or by slug.

        A slug works directly, so a URL the user pasted (`pelando.com.br/d/<slug>`) can be handed
        over with just the last path segment.
        """
        deal = await api.get_deal(get_client(), id_or_slug)
        return {"deal": deal_to_dict(deal), "note": SCOPE_NOTE}

    @mcp.tool()
    async def get_deal_comments(id_or_slug: str, limit: int = 50) -> dict:
        """Read a deal's comment thread — where the crowd corrects a bad posting.

        The thread is the only place a fake discount gets explained; there is no report or flag
        field in Pelando's data. Note the upstream endpoint has no pagination and returns the whole
        tree at once, so `limit` truncates locally and `truncated` tells you when that happened.
        """
        thread = await api.get_comments(get_client(), id_or_slug, limit=limit)
        return {
            "total_comments": thread.total_comments,
            "truncated": thread.truncated,
            "comments": [
                {
                    "author": c.user.nickname if c.user else None,
                    "is_admin": c.user.is_admin if c.user else False,
                    "content": c.content,
                    "deleted": c.is_deleted,
                    "reactions": {
                        "like": c.reactions.like_count,
                        "haha": c.reactions.haha_count,
                        "useful": c.reactions.useful_count,
                    },
                    "created_at": c.created_at,
                    "reply_count": len(c.replies),
                }
                for c in thread.comments
            ],
        }

    @mcp.tool()
    async def assess_deal_quality(id_or_slug: str, include_comments: bool = True) -> dict:
        """Judge whether the community believes a deal is genuine.

        This is what Pelando has that a price comparator does not. It reads the crowd's vote
        (`temperature`, which goes NEGATIVE when users think a discount is fake or the price was
        inflated beforehand), the comment reactions, the poster's reputation, the deal's age and
        status, and any condition declared in the title.

        Returns a verdict WITH its evidence attached, never a bare score — present the reasoning to
        the user rather than the label alone. It judges community sentiment, not merchant truth:
        it cannot verify that the price is real or still available.
        """
        client = get_client()
        deal = await api.get_deal(client, id_or_slug)
        thread = None
        if include_comments and deal.comment_count:
            thread = await api.get_comments(client, deal.id, limit=100)
        return assess(deal, thread).model_dump(mode="json")
