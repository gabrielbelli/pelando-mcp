"""Stores, coupons and the community taxonomy."""

from __future__ import annotations

from typing import Any

from .. import api
from ..models import COMMUNITY_SLUGS, ELECTRONICS_COMMUNITIES
from ..normalise import looks_like_coupon_code, strip_html
from .common import get_client


def register_merchant_tools(mcp: Any) -> None:
    @mcp.tool()
    async def search_stores(query: str, include_coupons: bool = True, limit: int = 10) -> dict:
        """Look up a merchant: its live activity counts and its top coupons.

        Matching is loose and substring-based upstream — "amazon" also returns "Amaro" — so results
        are ranked here by how closely the name matches, but the caller should still check the name
        before acting on the first row.

        `promotion_count` tells you how many active promotions a store has, but there is **no
        endpoint anywhere that lists them**. To see a store's deals, search a product term and
        filter by store.
        """
        stores = await api.search_stores(get_client(), query)
        folded = query.strip().lower()
        stores.sort(key=lambda s: (folded not in s.name.lower(), len(s.name)))

        out = []
        for store in stores[: max(1, limit)]:
            record: dict[str, Any] = {
                "name": store.name,
                "slug": store.slug,
                "url": store.url,
                "active_promotions": store.promotion_count,
                "coupon_count": store.coupon_count,
            }
            if include_coupons and store.coupons:
                record["coupons"] = [_coupon_to_dict(c) for c in store.coupons]
                if store.coupon_count and len(store.coupons) < store.coupon_count:
                    record["coupons_note"] = (
                        f"{len(store.coupons)} of {store.coupon_count} coupons are exposed by the "
                        f"API; the rest are only on the store's page, which exists for 5 partner "
                        f"stores only."
                    )
            out.append(record)
        return {"query": query, "stores": out}

    @mcp.tool()
    async def get_store_coupons(store: str, only_valid_codes: bool = True) -> dict:
        """Fetch a store's coupon codes.

        `only_valid_codes` drops entries whose code is prose rather than a redeemable code — the
        field has been seen holding instructions like "Resgatar cupom abaixo do produto".

        Coupons expire silently. Every code is reported with the date it was posted so its age is
        visible; none of them is guaranteed to still work.
        """
        stores = await api.search_stores(get_client(), store)
        if not stores:
            return {"store": store, "coupons": [], "note": f"No store matching {store!r}."}
        best = min(stores, key=lambda s: (store.strip().lower() not in s.name.lower(), len(s.name)))
        coupons = [_coupon_to_dict(c) for c in best.coupons]
        if only_valid_codes:
            coupons = [c for c in coupons if c.get("code_looks_valid")]
        return {
            "store": best.name,
            "slug": best.slug,
            "coupons": coupons,
            "note": "Coupons expire silently. Check the posted date before relying on a code.",
        }

    @mcp.tool()
    async def list_communities(refresh: bool = False) -> dict:
        """List Pelando's 11 communities — the site's only browse taxonomy.

        There is no category system beside this: deals carry a bare numeric `categoryId` with no
        label anywhere in the data. `tech-lover` and `mundo-gamer` are the electronics ones.
        """
        communities = await api.list_communities(get_client())
        return {
            "communities": [
                {"slug": c.slug, "name": c.name, "description": c.description}
                for c in communities
            ],
            "electronics": list(ELECTRONICS_COMMUNITIES),
            "count": len(COMMUNITY_SLUGS),
        }


def _coupon_to_dict(coupon: Any) -> dict[str, Any]:
    code: str | None = coupon.code
    return {
        "title": coupon.title,
        "code": code,
        "code_looks_valid": looks_like_coupon_code(code),
        "discount_percentage": coupon.discount_percentage,
        "discount_fixed": coupon.discount_fixed,
        "rules": strip_html(coupon.rules_description or coupon.description),
        "status": coupon.status,
        "temperature": coupon.temperature,
        "posted_at": coupon.created_at,
        "url": coupon.source_url,
    }
