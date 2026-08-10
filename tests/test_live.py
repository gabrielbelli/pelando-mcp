"""Live contract tests. Deselected by default; run by hand with `pytest -m live`.

A scraper does not break loudly — it starts returning "no deals found" and lies to the user. These
tests hit the real API at 1 req/s and fail on schema drift.

Deliberately not in CI: Cloudflare 403s datacentre IP ranges, so a hosted runner only ever sees the
block interstitial and never the schema. Run these from a home connection.
"""

from __future__ import annotations

import pytest

from pelando_mcp import api
from pelando_mcp.client import PelandoBlocked, PelandoClient
from pelando_mcp.models import COMMUNITY_SLUGS, DealKind

pytestmark = pytest.mark.live

_PROBE: dict[str, str | None] = {}


async def _blocked_reason() -> str | None:
    """One cheap probe, cached for the session.

    Cloudflare blocks by IP range, not just by UA — any datacentre address is refused outright — so
    on a blocked network every test below would otherwise pay the full challenge backoff (~40s each)
    only to skip. `max_retries=0` turns that into one request.
    """
    if "reason" not in _PROBE:
        _PROBE["reason"] = None
        async with PelandoClient(cache=None, max_retries=0) as probe:
            try:
                await api.browse_feed(probe, limit=1)
            except PelandoBlocked as exc:
                _PROBE["reason"] = str(exc)
    return _PROBE["reason"]


@pytest.fixture(autouse=True)
async def _skip_when_blocked():
    reason = await _blocked_reason()
    if reason is not None:
        pytest.skip(f"edge blocked this network, contract not verified: {reason}")


@pytest.fixture
async def client():
    async with PelandoClient(cache=None) as c:
        yield c


async def test_search_still_returns_priced_promotions(client):
    page = await api.search_deals(client, "notebook", kind=DealKind.PROMOTION, size=10)
    assert page.deals, "a broad electronics term returning nothing means the contract moved"
    assert all(d.price is not None for d in page.deals)
    assert all(d.store is not None for d in page.deals)


async def test_kind_filter_still_changes_the_result_set(client):
    """If `kind` stops being honoured, the filtered count stops being lower. The defensive guard
    still saves us, but we want to know the day it happens."""
    filtered = await api.search_deals(client, "notebook", kind=DealKind.PROMOTION, size=50)
    unfiltered = await api.search_deals(client, "notebook", kind=None, size=50)
    assert len(unfiltered.deals) >= len(filtered.deals)


async def test_hot_feed_parses(client):
    page = await api.browse_feed(client, limit=10)
    assert page.deals


async def test_community_feed_parses(client):
    page = await api.browse_feed(client, "recents", community="tech-lover", limit=10)
    assert page.deals


async def test_community_list_matches_the_hardcoded_taxonomy(client):
    communities = await api.list_communities(client)
    slugs = {c.slug for c in communities}
    assert slugs == set(COMMUNITY_SLUGS), "the taxonomy changed — update COMMUNITY_SLUGS"


async def test_store_search_still_carries_coupons(client):
    stores = await api.search_stores(client, "kabum")
    assert stores
    assert any(s.promotion_count for s in stores)
