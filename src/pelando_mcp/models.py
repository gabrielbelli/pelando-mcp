"""Pydantic models for the pelando.com.br JSON API.

Nullability here is not aspirational. Every `= None` default corresponds to a key that was observed
*absent* — a `KeyError`, not a `null` — in live payloads during reconnaissance, and every `| None`
to one observed null. `Optional[X]` without a default still raises on a missing key, so the defaults
are load-bearing. See PLAN.md §1.

Three naming traps are corrected here rather than passed on to callers:

- `date` and `lastActivityAt` are pre-humanised pt-BR strings ("4 dias", "ontem", "set 2023"), not
  timestamps, despite the `At` suffix on the second. They are exposed as `relative_*`.
- `price: 0` is legitimate and common (free Steam/Epic/Xbox games), and `price` is also nullable.
  Anything testing `if not deal.price` mislabels free deals as price-unknown.
- `temperature` goes negative. Nearly half of some archives are ≤ 0. No unsigned assumptions.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

API_BASE = "https://api-web.pelando.com.br"
WEB_BASE = "https://www.pelando.com.br"

MAX_PAGE_SIZE = 50
"""Server-enforced cap on `size` (/feed/search) and `limit` (/feed/v2/*). Over it: HTTP 400."""

SEARCH_TOTAL_CEILING = 10_000
"""`pageInfo.total` saturates here on broad terms. Never render it as a match count."""

COMMUNITY_SLUGS: tuple[str, ...] = (
    "tech-lover",
    "mundo-gamer",
    "achadinhos-importados",
    "tudo-gratis",
    "para-meu-lar",
    "para-elas",
    "para-eles",
    "cultura",
    "e-meme-ou-promo",
    "esporte-e-vida",
    "para-minha-familia",
)
"""The complete taxonomy — there is no category system besides this. An unknown slug is silently
IGNORED by the API (HTTP 200, unfiltered feed), so it must be validated before the request."""

ELECTRONICS_COMMUNITIES: tuple[str, ...] = ("tech-lover", "mundo-gamer")


class DealKind(StrEnum):
    PROMOTION = "promotion"
    DISCUSSION = "discussion"
    COUPON = "coupon"


class SortOption(StrEnum):
    """Verified by provoking a 400: `sortOption must be one of the following values:
    createdAt, temperature, lastCommentedAt`."""

    CREATED_AT = "createdAt"
    TEMPERATURE = "temperature"
    LAST_COMMENTED_AT = "lastCommentedAt"


class FeedName(StrEnum):
    HOTTEST = "hottest"
    RECENTS = "recents"


class DealStatus(StrEnum):
    ACTIVE = "active"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class Condition(StrEnum):
    NEW = "new"
    REEMBALADO = "reembalado"
    USED = "usado"
    REFURBISHED = "recondicionado"
    OPEN_BOX = "open_box"
    UNKNOWN = "unknown"


class Verdict(StrEnum):
    """Outcome of `assess_deal_quality`."""

    CROWD_APPROVED = "crowd_approved"
    MIXED = "mixed"
    CROWD_REJECTED = "crowd_rejected"
    INSUFFICIENT_SIGNAL = "insufficient_signal"


class Author(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    nickname: str | None = None
    top_creator: bool = Field(default=False, alias="topCreator")
    is_creator: bool = Field(default=False, alias="isCreator")
    # Absent key (not null) on some live deals — the default is required.
    profile_picture: str | None = Field(default=None, alias="profilePicture")


class Store(BaseModel):
    """One merchant.

    The API returns three different shapes for this object depending on the endpoint
    (`/stores` → 8 fields, `/stores/search` and `/stores/all` → 12, `/stores/{slug}` → 21), so
    everything past the identity fields is optional.

    `logo` is a bare storage key in feed payloads ("s/535_1.png", sometimes with a leading slash,
    inconsistently within a single response) and a signed absolute URL elsewhere. It cannot be
    turned into a working URL by joining — media.pelando.com.br is a thumbor with a per-transform
    HMAC. Join to the cached `/stores/all` record by slug to get a usable one.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    name: str
    # Not always kebab-case: bare domains like "terabyteshop.com.br" are common and valid.
    slug: str | None = None
    url: str | None = None
    logo: str | None = None
    linking_enabled: bool | None = Field(default=None, alias="linkingEnabled")
    page_enabled: bool | None = Field(default=None, alias="pageEnabled")
    promotion_count: int | None = Field(default=None, alias="promotionCount")
    coupon_count: int | None = Field(default=None, alias="couponCount")
    # Populated only by /stores/search. The SEO fields from /stores/{slug} (pageTitle and friends)
    # are deliberately not modelled: they contain unrendered mustache templates such as
    # "Cupom {{storeName}} | Até {{discount}}", which must never reach a user-facing answer.
    coupons: list[Coupon] = Field(default_factory=list)


class Deal(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    slug: str | None = None
    title: str
    kind: DealKind = DealKind.PROMOTION
    status: DealStatus = DealStatus.UNKNOWN

    # Can be negative: the crowd voting a listing down. This is the point of the whole server.
    temperature: int = 0
    comment_count: int = Field(default=0, alias="commentCount")

    # 0 is a real price (free games). Test `price is None`, never falsiness.
    price: float | None = None
    discount_percentage: int | None = Field(default=None, alias="discountPercentage")
    discount_fixed: float | None = Field(default=None, alias="discountFixed")
    # Tri-state. Never observed true, so `False` and "unknown" must stay distinguishable.
    free_shipping: bool | None = Field(default=None, alias="freeShipping")
    coupon_code: str | None = Field(default=None, alias="couponCode")

    # The only real timestamps.
    created_at: datetime | None = Field(default=None, alias="createdAt")
    first_approved_at: datetime | None = Field(default=None, alias="firstApprovedAt")
    # Humanised pt-BR strings, NOT timestamps, despite `lastActivityAt`'s suffix.
    relative_date: str | None = Field(default=None, alias="date")
    relative_last_activity: str | None = Field(default=None, alias="lastActivityAt")

    # Absent key on every `discussion` row.
    store: Store | None = None
    author: Author | None = None

    # The direct merchant link. Occurs as both null and "" — truthiness check, not `is not None`.
    source_url: str | None = Field(default=None, alias="sourceUrl")
    image_url: str | None = Field(default=None, alias="imageUrl")
    # `redirectUrl` (a dpl.pelando.com.br affiliate JWT, regenerated on every request) is
    # deliberately not modelled. We link users to the merchant, not through a tracker.

    short_description: str | None = Field(default=None, alias="shortDescription")
    cashback: Any | None = None

    @field_validator("source_url", "coupon_code", "short_description", mode="before")
    @classmethod
    def _empty_string_is_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("status", mode="before")
    @classmethod
    def _unknown_status(cls, v: Any) -> Any:
        if isinstance(v, str) and v not in {s.value for s in DealStatus}:
            return DealStatus.UNKNOWN
        return v

    @property
    def is_expired(self) -> bool:
        return self.status is DealStatus.EXPIRED

    @property
    def has_price(self) -> bool:
        return self.price is not None

    @property
    def web_url(self) -> str | None:
        return f"{WEB_BASE}/d/{self.slug}" if self.slug else None


class PageInfo(BaseModel):
    """`/feed/search` returns only {page, total}. There is no `hasNextPage`, and `page` merely
    echoes the input (send page=-1, get back -1). `total` saturates at 10000 on broad terms, so
    the only safe next-page rule is `len(deals) == size`."""

    model_config = ConfigDict(extra="ignore")

    page: int = 1
    total: int = 0

    @property
    def total_is_saturated(self) -> bool:
        return self.total >= SEARCH_TOTAL_CEILING


class DealPage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    deals: list[Deal] = Field(default_factory=list)
    page_info: PageInfo = Field(default_factory=PageInfo)
    has_next_page: bool = False
    total_is_estimate: bool = False
    dropped_non_offers: int = 0
    """Rows discarded because they carried no price or no store — discussion threads that slipped
    past `kind=promotion`."""


class Reactions(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    like_count: int = Field(default=0, alias="likeCount")
    # The crowd laughing at a deal. Paired with a negative temperature this is the fake-discount
    # tell — there is no report or flag field anywhere in the payload.
    haha_count: int = Field(default=0, alias="hahaCount")
    useful_count: int = Field(default=0, alias="usefulCount")


class Specialty(BaseModel):
    """A commenter's badge, e.g. `{"key": "genio-tech", "name": "Gênio Tech", "image": ...}`.

    Reconnaissance recorded this as a plain string; live payloads return an object. The validator
    below accepts both so a revert upstream cannot break parsing.
    """

    model_config = ConfigDict(extra="ignore")

    key: str | None = None
    name: str | None = None
    image: str | None = None


class CommentUser(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = None
    nickname: str | None = None
    is_admin: bool = Field(default=False, alias="isAdmin")
    is_creator: bool = Field(default=False, alias="isCreator")
    specialty: Specialty | None = None

    @field_validator("specialty", mode="before")
    @classmethod
    def _specialty_from_string(cls, v: Any) -> Any:
        if isinstance(v, str):
            return {"name": v}
        return v


class Comment(BaseModel):
    """Deleted comments are returned, not filtered: `content: null`, `status: "deleted"`.

    `replies` is flattened to two levels — a five-message chain sits as five siblings in one root's
    `replies`, each `reply_to_id` pointing at the previous. Rendering `replies` as literal
    indentation misrepresents the conversation; rebuild from `reply_to_id` if the shape matters.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str
    content: str | None = None
    user: CommentUser | None = None
    reactions: Reactions = Field(default_factory=Reactions)
    reply_to_id: str | None = Field(default=None, alias="replyToId")
    replies: list[Comment] = Field(default_factory=list)
    sensitive: bool = False
    status: str | None = None
    deleted_at: datetime | None = Field(default=None, alias="deletedAt")
    created_at: datetime | None = Field(default=None, alias="createdAt")

    @property
    def is_deleted(self) -> bool:
        return self.status == "deleted" or self.deleted_at is not None


class CommentThread(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    # Counts non-deleted nodes at every depth, so it matches neither the root count nor the node
    # count. Reported as-is rather than recomputed, but do not rely on it.
    total_comments: int = Field(default=0, alias="totalComments")
    comments: list[Comment] = Field(default_factory=list)
    truncated: bool = False


class Coupon(BaseModel):
    """A coupon. `description` / `rules_description` arrive as HTML and are stripped on the way out.

    `code` is reported raw. Whether it is actually redeemable is a separate question — the field
    has been observed holding the sentence "Resgatar cupom abaixo do produto" — so callers should
    consult `normalise.looks_like_coupon_code` before presenting it as one.
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    id: str | None = None
    title: str | None = None
    slug: str | None = None
    code: str | None = Field(default=None, alias="couponCode")
    description: str | None = None
    rules_description: str | None = Field(default=None, alias="rulesDescription")
    discount_percentage: int | None = Field(default=None, alias="discountPercentage")
    discount_fixed: float | None = Field(default=None, alias="discountFixed")
    free_shipping: bool | None = Field(default=None, alias="freeShipping")
    status: str | None = None
    temperature: int = 0
    comment_count: int = Field(default=0, alias="commentCount")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    source_url: str | None = Field(default=None, alias="sourceUrl")


class Community(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    slug: str
    name: str | None = None
    # Plain text on communities — unlike store descriptions, do not strip this one.
    description: str | None = None


class Signal(BaseModel):
    """One piece of evidence behind an assessment."""

    name: str
    value: str
    reading: str


class DealAssessment(BaseModel):
    """The crowd's verdict on a deal, with its evidence attached.

    Deliberately not a single number. A score invites the calling model to quote it as fact;
    evidence invites it to explain, and lets the user disagree.
    """

    deal_id: str
    title: str
    verdict: Verdict
    signals: list[Signal] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    temperature: int = 0
    condition: Condition = Condition.UNKNOWN
    price: float | None = None
    store_name: str | None = None
    posted_at: datetime | None = None
    age_days: float | None = None
    status: DealStatus = DealStatus.UNKNOWN
    caveat: str = (
        "Reflects what Pelando users voted and commented, not a verified merchant price. "
        "The price shown is the number the poster typed when they posted."
    )


# Store references Coupon, which is defined after it; Comment references itself.
Store.model_rebuild()
Comment.model_rebuild()
