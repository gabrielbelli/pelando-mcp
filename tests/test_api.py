from __future__ import annotations

import httpx
import pytest
import respx

from pelando_mcp import api
from pelando_mcp.client import (
    API_BASE,
    PelandoBlocked,
    PelandoClient,
    PelandoError,
    PelandoNotFound,
    unwrap,
)
from pelando_mcp.models import DealKind, FeedName


@pytest.fixture
def client() -> PelandoClient:
    # No cache, no robots fetch: this exercises the parsing and validation, not the transport.
    return PelandoClient(cache=None, respect_robots=False, max_retries=0)


@respx.mock
async def test_search_drops_price_null_rows(client, search_mixed):
    """The defensive guard behind `kind=promotion`.

    The parameter is undocumented and Pelando's own client never sends it. If it stops being
    honoured, a third of an electronics result set becomes price-null discussion rows.
    """
    respx.get(url__startswith=f"{API_BASE}/feed/search").mock(
        return_value=httpx.Response(200, json=search_mixed)
    )
    raw_count = len(search_mixed["data"]["deals"])
    page = await api.search_deals(client, "notebook", kind=DealKind.PROMOTION, size=50)
    assert page.dropped_non_offers > 0, "fixture should contain discussion rows"
    assert len(page.deals) + page.dropped_non_offers == raw_count
    assert all(d.price is not None and d.store is not None for d in page.deals)


@respx.mock
async def test_search_sends_hide_expired_by_default(client, search_rtx):
    route = respx.get(url__startswith=f"{API_BASE}/feed/search").mock(
        return_value=httpx.Response(200, json=search_rtx)
    )
    await api.search_deals(client, "rtx 5070")
    # Upstream default is FALSE — expired deals come back unless we opt out, and they dominate a
    # temperature sort because they had time to accumulate heat.
    assert "hideExpired=true" in str(route.calls[0].request.url)


@respx.mock
async def test_has_next_page_uses_page_length_not_total(client, search_rtx):
    """`total` saturates at 10000 and `page` echoes the input, so neither can decide this."""
    respx.get(url__startswith=f"{API_BASE}/feed/search").mock(
        return_value=httpx.Response(200, json=search_rtx)
    )
    returned = len(search_rtx["data"]["deals"])
    full = await api.search_deals(client, "rtx 5070", kind=None, size=returned)
    assert full.has_next_page
    partial = await api.search_deals(client, "rtx 5070", kind=None, size=returned + 1)
    assert not partial.has_next_page


@respx.mock
async def test_empty_result_is_not_an_error(client, search_empty):
    respx.get(url__startswith=f"{API_BASE}/feed/search").mock(
        return_value=httpx.Response(200, json=search_empty)
    )
    page = await api.search_deals(client, "zzqqxwv-nao-existe")
    assert page.deals == []
    assert not page.has_next_page


@respx.mock
async def test_size_is_clamped_below_the_server_cap(client, search_rtx):
    """`size` over 50 is a guaranteed 400. Clamp rather than burn a rate-limited request."""
    route = respx.get(url__startswith=f"{API_BASE}/feed/search").mock(
        return_value=httpx.Response(200, json=search_rtx)
    )
    await api.search_deals(client, "notebook", size=500)
    assert "size=50" in str(route.calls[0].request.url)


async def test_unknown_community_is_rejected_before_the_request(client):
    """The API silently ignores a bad slug and returns the global feed — the user would be shown
    everything and told it was tech."""
    with pytest.raises(PelandoError, match="unknown community"):
        await api.browse_feed(client, FeedName.RECENTS, community="nao-existe-xyz")


@respx.mock
async def test_browse_feed_reads_cursor_page_info(client, feed_hottest):
    respx.get(url__startswith=f"{API_BASE}/feed/v2/hottest").mock(
        return_value=httpx.Response(200, json=feed_hottest)
    )
    page = await api.browse_feed(client, FeedName.HOTTEST)
    assert page.deals
    # /feed/v2/* uses hasNextPage; /feed/search does not have it at all.
    assert page.has_next_page is bool(feed_hottest["data"]["pageInfo"]["hasNextPage"])


@respx.mock
async def test_get_deal_accepts_a_slug(client, deal_detail):
    respx.get(f"{API_BASE}/deals/notebook-asus-tuf-a16").mock(
        return_value=httpx.Response(200, json=deal_detail)
    )
    deal = await api.get_deal(client, "notebook-asus-tuf-a16")
    assert deal.id


@respx.mock
async def test_comments_are_truncated_locally(client, deal_comments):
    """The endpoint ignores `limit` and returns the whole tree, so truncation is ours to do."""
    respx.get(f"{API_BASE}/deals/x/comments").mock(
        return_value=httpx.Response(200, json=deal_comments)
    )
    thread = await api.get_comments(client, "x", limit=2)
    assert len(thread.comments) <= 2
    if len(deal_comments["data"]["comments"]) > 2:
        assert thread.truncated


@respx.mock
async def test_challenge_body_is_treated_as_a_block_not_content(client):
    """Parsing a challenge page yields zero results, which looks exactly like "no deals found"."""
    respx.get(url__startswith=f"{API_BASE}/feed/search").mock(
        return_value=httpx.Response(200, html="<html><title>Just a moment...</title></html>")
    )
    with pytest.raises(PelandoBlocked):
        await api.search_deals(client, "rtx 5070")


@respx.mock
async def test_not_found_uses_the_application_error_envelope(client):
    respx.get(f"{API_BASE}/deals/missing").mock(
        return_value=httpx.Response(
            404, json={"statusCode": 404, "errorMessage": "Deal not found"}
        )
    )
    with pytest.raises(PelandoNotFound, match="Deal not found"):
        await api.get_deal(client, "missing")


def test_unwrap_handles_both_error_envelopes():
    """Application errors use `errorMessage`; unknown routes use NestJS's `message` + `error`.
    Only `statusCode` is common."""
    assert unwrap({"data": {"deals": []}, "timestamp": "x"}) == {"deals": []}
    with pytest.raises(PelandoNotFound):
        unwrap({"statusCode": 404, "errorMessage": "Deal not found"})
    with pytest.raises(PelandoError):
        unwrap({"message": "Cannot GET /products/search", "error": "Not Found", "statusCode": 404})


async def test_params_may_not_be_inlined(client):
    """Passing `term` twice yields 400 "term must be a string" — build params in one place."""
    with pytest.raises(ValueError):
        await client.get_json("/feed/search?term=x", {"term": "y"}, ttl=1)
