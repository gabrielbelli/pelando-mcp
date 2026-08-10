from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from . import __version__
from .client import DEFAULT_USER_AGENT
from .tools import register_deal_tools, register_merchant_tools

server = MCPServer(
    "pelando-mcp",
    version=__version__,
    instructions=(
        "Reads pelando.com.br, a Brazilian community deal board. It reports what users posted and "
        "how the community voted — not live shelf prices, and not a product catalogue. Pelando has "
        "no product entity, so there is no multi-store price table for a SKU and no price history. "
        "A deal's price is the number its poster typed at the time they posted it. Say so when "
        "presenting one."
    ),
)

register_deal_tools(server)
register_merchant_tools(server)


@server.tool()
def ping() -> dict:
    """Liveness check. Returns server name, version, and runtime config."""
    return {
        "server": "pelando-mcp",
        "version": __version__,
        "source": "pelando.com.br (community deals only — no product catalogue)",
        "user_agent": os.getenv("PELANDO_USER_AGENT", DEFAULT_USER_AGENT),
        "rate_limit_rps": float(os.getenv("PELANDO_RATE_LIMIT_RPS", "1.0")),
        "cache_path": os.getenv("PELANDO_CACHE_PATH", "data/cache.sqlite"),
    }


def main() -> None:
    server.run()


if __name__ == "__main__":
    main()
