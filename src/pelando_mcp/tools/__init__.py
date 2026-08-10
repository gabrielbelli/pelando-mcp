"""MCP tool registration."""

from .deals import register_deal_tools
from .merchants import register_merchant_tools

__all__ = ["register_deal_tools", "register_merchant_tools"]
