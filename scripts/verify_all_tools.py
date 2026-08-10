#!/usr/bin/env python
"""End-to-end smoke test of every MCP tool against the live site.

Not part of the test suite: this hits the network and writes its output to `data/verify/` so the
responses can be eyeballed. Runs at the server's normal 1 req/s.

    ./venv/bin/python scripts/verify_all_tools.py
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from pelando_mcp.server import server

OUT = Path(__file__).resolve().parent.parent / "data" / "verify"


def _payload(result: Any) -> Any:
    """Pull the structured content out of a CallToolResult."""
    data = getattr(result, "structuredContent", None)
    if data is not None:
        return data
    blocks = getattr(result, "content", None) or []
    out = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            out.append(json.loads(text))
        except ValueError:
            out.append(text)
    return out[0] if len(out) == 1 else out


async def run(step: int, name: str, args: dict[str, Any]) -> Any:
    print(f"[{step:02d}] {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})")
    try:
        payload = _payload(await server.call_tool(name, args))
    except Exception as exc:  # noqa: BLE001 - a verification script reports, it does not raise
        payload = {"error": f"{type(exc).__name__}: {exc}"}
        print(f"     !! {payload['error']}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{step:02d}_{name}.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    )
    return payload


async def main() -> None:
    await run(1, "ping", {})
    await run(2, "list_communities", {})

    search = await run(3, "search_deals", {"query": "rtx 5070", "size": 10})
    deals = (search or {}).get("deals") or (search or {}).get("related") or []
    if not deals:
        # Entirely normal on Pelando: a term can have no live postings at all. Fall back to a
        # broad one so the remaining tools still get exercised.
        print("     -- no live deals for that term; falling back to 'notebook'")
        search = await run(4, "search_deals", {"query": "notebook", "size": 10})
        deals = (search or {}).get("deals") or []

    await run(5, "search_deals", {"query": "iphone 16 pro", "include_expired": True, "size": 10})
    await run(6, "browse_feed", {"feed": "hottest", "limit": 10})
    await run(7, "browse_feed", {"feed": "recents", "community": "tech-lover", "limit": 10})

    if deals:
        deal_id = deals[0]["id"]
        await run(8, "get_deal", {"id_or_slug": deal_id})
        await run(9, "get_deal_comments", {"id_or_slug": deal_id, "limit": 20})
        await run(10, "assess_deal_quality", {"id_or_slug": deal_id})

        # Find something the crowd disliked — the signal this server exists for.
        cold = await run(
            11, "search_deals", {"query": "rtx", "sort": "createdAt", "include_expired": True,
                                 "size": 50, "drop_irrelevant": False}
        )
        candidates = [d for d in (cold or {}).get("deals", []) if d.get("temperature", 0) < 0]
        if candidates:
            await run(12, "assess_deal_quality", {"id_or_slug": candidates[0]["id"]})
        else:
            print("     -- no negative-temperature deal in that sample")

    await run(13, "search_stores", {"query": "kabum"})
    await run(14, "get_store_coupons", {"store": "kabum"})

    print(f"\nWrote results to {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
