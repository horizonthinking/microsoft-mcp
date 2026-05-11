import os
import sys
from .auth import assert_configured
from .tools import mcp


def main() -> None:
    try:
        assert_configured()
    except Exception as exc:
        print(
            f"Error: Microsoft MCP authentication is not configured: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    mcp.run()


if __name__ == "__main__":
    main()
