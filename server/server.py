"""MCP entrypoint — wires up the FastMCP instance defined in app.py with its tools, prompts,
and resources.

Run with `uv run python -m server.server`, or point an MCP client (Claude Desktop/Code) at
this module over stdio. Vault root defaults to the repo root but can be overridden via
MYPHD_TRACKER_ROOT for testing against a scratch vault (see app.py).

Actual tool/prompt/resource definitions live in tools.py/prompts.py/resources.py respectively
— each registers itself against `mcp` (from app.py) as a side effect of being imported here.
"""

from __future__ import annotations

from server import (  # noqa: F401 — imported for registration side effects
    prompts,
    resources,
    tools,
)
from server.app import mcp


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
