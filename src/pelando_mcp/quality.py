"""The crowd's verdict on a deal.

This is the one thing this server does that no price comparator can, so it is worth being precise
about what the signals actually mean.

There is **no** report, flag, scam or "price went up" field anywhere in Pelando's payloads — that
was checked. What exists is a temperature that can go negative, three comment reaction counters,
poster reputation, and the deal's own age and status. Everything below is built from those, and
every conclusion carries its evidence so the user can disagree with it.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .models import (
    Comment,
    CommentThread,
    Condition,
    Deal,
    DealAssessment,
    DealStatus,
    Signal,
    Verdict,
)
from .normalise import detect_condition

HOT_TEMPERATURE = 300
"""Above this the crowd has clearly endorsed it. Chosen from observed live feeds, not tuned."""

QUIET_TEMPERATURE = 50
"""Below this, with no comments, there simply is not enough signal to say anything."""

STALE_DAYS = 7.0


def _age_days(deal: Deal, now: datetime | None = None) -> float | None:
    stamp = deal.first_approved_at or deal.created_at
    if stamp is None:
        return None
    now = now or datetime.now(UTC)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return round((now - stamp).total_seconds() / 86400.0, 2)


def _walk(comments: list[Comment]) -> list[Comment]:
    out: list[Comment] = []
    for comment in comments:
        out.append(comment)
        if comment.replies:
            out.extend(_walk(comment.replies))
    return out


def assess(
    deal: Deal,
    thread: CommentThread | None = None,
    *,
    now: datetime | None = None,
) -> DealAssessment:
    signals: list[Signal] = []
    warnings: list[str] = []

    # --- temperature: the crowd's vote, and the only one that can go negative -----------------
    if deal.temperature < 0:
        signals.append(
            Signal(
                name="temperature",
                value=str(deal.temperature),
                reading="negative — the crowd is actively voting this listing down",
            )
        )
        warnings.append(
            f"Negative temperature ({deal.temperature}). On Pelando that usually means users "
            f"judged the discount fake, the price inflated beforehand, or the listing misleading."
        )
    elif deal.temperature >= HOT_TEMPERATURE:
        signals.append(
            Signal(
                name="temperature",
                value=str(deal.temperature),
                reading="strongly upvoted by the community",
            )
        )
    else:
        signals.append(
            Signal(
                name="temperature",
                value=str(deal.temperature),
                reading="modest — neither endorsed nor rejected",
            )
        )

    # --- comment reactions: no flag field exists, so laughter is the tell ----------------------
    haha = like = useful = 0
    if thread is not None:
        for comment in _walk(thread.comments):
            haha += comment.reactions.haha_count
            like += comment.reactions.like_count
            useful += comment.reactions.useful_count
        if haha or like or useful:
            signals.append(
                Signal(
                    name="reactions",
                    value=f"{like} like / {haha} haha / {useful} useful",
                    reading="aggregate across the whole comment tree",
                )
            )
        if haha > like and haha > 0:
            warnings.append(
                f"The comment thread is laughing at this deal ({haha} haha vs {like} like)."
            )
        if useful > 0:
            signals.append(
                Signal(
                    name="useful_comments",
                    value=str(useful),
                    reading="someone posted a correction the crowd found useful — worth reading",
                )
            )

    # --- poster reputation ---------------------------------------------------------------------
    if deal.author is not None:
        if deal.author.top_creator:
            signals.append(
                Signal(name="author", value=deal.author.nickname or deal.author.id,
                       reading="top creator — an established poster")
            )
        elif deal.author.is_creator:
            signals.append(
                Signal(name="author", value=deal.author.nickname or deal.author.id,
                       reading="creator account")
            )

    # --- status and age ------------------------------------------------------------------------
    age = _age_days(deal, now)
    if deal.status is DealStatus.EXPIRED:
        warnings.append("This deal is marked expired. The price is historical, not available now.")
    if age is not None:
        reading = "posted recently" if age <= STALE_DAYS else "old — confidence decays with age"
        signals.append(Signal(name="age_days", value=str(age), reading=reading))
        if age > STALE_DAYS and deal.status is not DealStatus.EXPIRED:
            warnings.append(
                f"Posted {age:.0f} days ago and still marked active. Pelando's status is "
                f"maintained by users and moderators, not verified against the merchant."
            )

    # --- condition ------------------------------------------------------------------------------
    condition = detect_condition(deal.title)
    if condition is not Condition.UNKNOWN:
        signals.append(
            Signal(name="condition", value=condition.value,
                   reading="declared in the title — this is not a new-unit price")
        )
        warnings.append(
            f"The title declares this as '{condition.value}'. Do not compare it against new-unit "
            f"prices."
        )

    if deal.price is None:
        warnings.append("No price on this posting — it is a discussion or coupon entry, not an offer.")

    # --- verdict ---------------------------------------------------------------------------------
    if deal.temperature < 0 or (haha > like and haha > 0):
        verdict = Verdict.CROWD_REJECTED
    elif deal.temperature >= HOT_TEMPERATURE and deal.status is not DealStatus.EXPIRED:
        verdict = Verdict.CROWD_APPROVED
    elif deal.temperature < QUIET_TEMPERATURE and deal.comment_count == 0:
        verdict = Verdict.INSUFFICIENT_SIGNAL
    else:
        verdict = Verdict.MIXED

    return DealAssessment(
        deal_id=deal.id,
        title=deal.title,
        verdict=verdict,
        signals=signals,
        warnings=warnings,
        temperature=deal.temperature,
        condition=condition,
        price=deal.price,
        store_name=deal.store.name if deal.store else None,
        posted_at=deal.first_approved_at or deal.created_at,
        age_days=age,
        status=deal.status,
    )
