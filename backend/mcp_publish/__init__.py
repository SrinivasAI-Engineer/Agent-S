"""
MCP Publishing Server for AgentSocialS.

Exposes tools: publish_post, upload_media.
Run with: python -m mcp_publish.server
"""

from mcp_publish.server import run

__all__ = ["run"]
