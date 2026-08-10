from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pelando_mcp.models import (
    Author,
    Comment,
    CommentThread,
    Deal,
    DealStatus,
    Reactions,
    Verdict,
)
from pelando_mcp.quality import assess


def _deal(**kwargs) -> Deal:
    base = {
        "id": "abc",
        "title": "Placa de Video RTX 5070 12GB",
        "temperature": 400,
        "status": "active",
        "price": 4129.0,
        "createdAt": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    }
    base.update(kwargs)
    return Deal.model_validate(base)


def test_negative_temperature_is_a_rejection_not_a_quiet_listing(deal_negative):
    """The crowd downvoting a deal is the highest-value signal on the site. It must never be
    presented as just another result."""
    deal = Deal.model_validate(deal_negative["data"])
    result = assess(deal)
    assert result.verdict is Verdict.CROWD_REJECTED
    assert any("Negative temperature" in w for w in result.warnings)


def test_hot_active_deal_is_approved():
    result = assess(_deal(temperature=2300))
    assert result.verdict is Verdict.CROWD_APPROVED


def test_quiet_deal_reports_insufficient_signal():
    result = assess(_deal(temperature=10, commentCount=0))
    assert result.verdict is Verdict.INSUFFICIENT_SIGNAL


def test_laughter_outweighs_a_warm_temperature():
    """`hahaCount > likeCount` is the fake-discount tell — there is no report field in the data."""
    thread = CommentThread(
        totalComments=2,
        comments=[
            Comment(id="1", content="kkkk", reactions=Reactions(hahaCount=40, likeCount=2)),
        ],
    )
    result = assess(_deal(temperature=900), thread)
    assert result.verdict is Verdict.CROWD_REJECTED
    assert any("laughing" in w for w in result.warnings)


def test_expired_deal_is_flagged_even_when_hot():
    result = assess(_deal(temperature=5000, status="expired"))
    assert result.verdict is not Verdict.CROWD_APPROVED
    assert any("expired" in w for w in result.warnings)


def test_condition_in_title_produces_a_warning():
    result = assess(_deal(title="[REEMBALADO] GeForce RTX 5070 OC 12GB"))
    assert any("reembalado" in w.lower() for w in result.warnings)


def test_old_but_active_deal_warns_about_unverified_status():
    old = (datetime.now(UTC) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
    result = assess(_deal(firstApprovedAt=old))
    assert result.age_days and result.age_days > 7
    assert any("maintained by users" in w for w in result.warnings)


def test_top_creator_is_recorded_as_a_signal():
    deal = _deal()
    deal.author = Author(id="1", nickname="alguem", topCreator=True)
    result = assess(deal)
    assert any(s.name == "author" for s in result.signals)


def test_assessment_always_carries_its_evidence():
    result = assess(_deal())
    assert result.signals
    assert result.caveat
    assert result.status is DealStatus.ACTIVE
