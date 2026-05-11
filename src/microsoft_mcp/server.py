import os
import sys
from .auth import assert_configured
from .tools import mcp


def _http_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {}
    if host := os.getenv("MICROSOFT_MCP_HOST"):
        kwargs["host"] = host
    if port := os.getenv("MICROSOFT_MCP_PORT"):
        kwargs["port"] = int(port)
    if path := os.getenv("MICROSOFT_MCP_PATH"):
        kwargs["path"] = path
    if log_level := os.getenv("MICROSOFT_MCP_LOG_LEVEL"):
        kwargs["log_level"] = log_level
    return kwargs


def main() -> None:
    try:
        assert_configured()
    except Exception as exc:
        print(
            f"Error: Microsoft MCP authentication is not configured: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)

    transport = os.getenv("MICROSOFT_MCP_TRANSPORT", "stdio")
    kwargs = _http_kwargs() if transport in {"streamable-http", "sse"} else {}
    mcp.run(transport=transport, **kwargs)


if __name__ == "__main__":
    main()
