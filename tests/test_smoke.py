from __future__ import annotations

from pelando_mcp.server import ping, server

EXPECTED_TOOLS = {
    "search_deals",
    "browse_feed",
    "get_deal",
    "get_deal_comments",
    "assess_deal_quality",
    "search_stores",
    "get_store_coupons",
    "list_communities",
    "ping",
}


async def test_all_tools_are_registered():
    names = {tool.name for tool in await server.list_tools()}
    assert EXPECTED_TOOLS <= names


async def test_every_tool_describes_its_scope():
    """A tool that oversells itself makes the calling model lie to the user on its behalf."""
    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"


def test_ping():
    result = ping()
    assert result["server"] == "pelando-mcp"
    assert "community deals only" in result["source"]
