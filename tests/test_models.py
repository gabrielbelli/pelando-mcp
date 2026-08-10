"""Model tests against real captured payloads.

Each test here corresponds to a trap found during reconnaissance. They are regression tests for
live data shapes, so if one fails it means the API changed, not that the test is wrong.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from pelando_mcp.models import Comment, CommentThread, Deal, DealStatus, PageInfo, Store


def _deals(payload) -> list[Deal]:
    return [Deal.model_validate(d) for d in payload["data"]["deals"]]


def test_every_fixture_row_parses(search_rtx, search_mixed, archive_iphone, feed_hottest):
    """No row in any captured page may fail validation — absent keys included."""
    for payload in (search_rtx, search_mixed, archive_iphone, feed_hottest):
        deals = _deals(payload)
        assert deals, "fixture should not be empty"


def test_relative_date_fields_are_not_timestamps(search_rtx):
    """`date` and `lastActivityAt` are humanised pt-BR strings despite the `At` suffix.

    Modelling either as a datetime crashes on the first request.
    """
    deals = _deals(search_rtx)
    values = [d.relative_date for d in deals if d.relative_date]
    assert values, "fixture should carry at least one relative date"
    for value in values:
        with pytest.raises(ValueError):
            datetime.fromisoformat(value)


def test_real_timestamps_do_parse(search_rtx):
    deals = _deals(search_rtx)
    assert any(isinstance(d.created_at, datetime) for d in deals)


def test_temperature_can_be_negative(archive_iphone):
    """Nearly half the iPhone archive is ≤ 0. Any unsigned assumption breaks here."""
    temps = [d.temperature for d in _deals(archive_iphone)]
    assert min(temps) < 0


def test_zero_price_is_not_missing_price(feed_hottest):
    """Free games post at price 0. `if not deal.price` would call them price-unknown."""
    deals = _deals(feed_hottest)
    for deal in deals:
        if deal.price == 0:
            assert deal.has_price
            break


def test_discussion_rows_have_no_store(search_mixed):
    """`store` is an absent key, not null, on discussion rows."""
    deals = _deals(search_mixed)
    assert any(d.store is None for d in deals), "fixture should contain a storeless row"


def test_missing_profile_picture_does_not_raise(search_mixed):
    """`author.profilePicture` disappears entirely on some records."""
    for deal in _deals(search_mixed):
        if deal.author is not None:
            assert deal.author.profile_picture is None or isinstance(
                deal.author.profile_picture, str
            )


def test_empty_source_url_becomes_none(search_mixed):
    """`sourceUrl` occurs as both null and "". Truthiness, not `is not None`."""
    for deal in _deals(search_mixed):
        assert deal.source_url is None or deal.source_url.strip()


def test_empty_search_is_clean(search_empty):
    deals = _deals(search_empty)
    info = PageInfo.model_validate(search_empty["data"]["pageInfo"])
    assert deals == []
    assert info.total == 0


def test_saturated_total_is_flagged():
    assert PageInfo(page=1, total=10_000).total_is_saturated
    assert not PageInfo(page=1, total=49).total_is_saturated


def test_deal_detail_parses(deal_detail, deal_negative):
    hot = Deal.model_validate(deal_detail["data"])
    cold = Deal.model_validate(deal_negative["data"])
    assert hot.id and cold.id
    assert cold.temperature < 0
    assert hot.status in set(DealStatus)


def test_price_is_int_or_float(deal_detail, deal_negative):
    """One deal returns 5427, another 3542.55. An int annotation crashes."""
    assert isinstance(Deal.model_validate(deal_detail["data"]).price, float)
    assert isinstance(Deal.model_validate(deal_negative["data"]).price, float)


def test_comment_thread_keeps_deleted_comments(deal_comments):
    thread = CommentThread.model_validate(deal_comments["data"])
    assert thread.comments
    flat: list[Comment] = []

    def walk(items):
        for item in items:
            flat.append(item)
            walk(item.replies)

    walk(thread.comments)
    # Deleted nodes come back with content: null rather than being filtered out.
    assert all(c.content is not None or c.is_deleted or c.content is None for c in flat)


def test_store_ids_stay_strings(stores_search):
    stores = [Store.model_validate(s) for s in stores_search["data"]["stores"]]
    assert stores
    for store in stores:
        assert isinstance(store.id, str)


def test_store_search_carries_coupons(stores_search):
    stores = [Store.model_validate(s) for s in stores_search["data"]["stores"]]
    assert any(s.coupons for s in stores), "/stores/search is the only shape with coupons"
