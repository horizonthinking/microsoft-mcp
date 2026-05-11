import json
import os
import pathlib as pl
import sys
from typing import Any, NamedTuple

import msal
from dotenv import load_dotenv

load_dotenv()

CACHE_FILE = pl.Path.home() / ".microsoft_mcp_token_cache.json"
SCOPES = ["https://graph.microsoft.com/.default"]
H4_AUTH_MODES = {"h4", "h4_user_profile", "user_profile", "mongodb"}
DEFAULT_AGENT_CLI_PATH = pl.Path.home() / "projects" / "local" / "agent-cli"
ENVVAR_PATHS = [
    pl.Path.home() / "local" / "env" / "envvar.json",
    pl.Path.home()
    / "Library"
    / "Mobile Documents"
    / "com~apple~CloudDocs"
    / "local"
    / "env"
    / "envvar.json",
]
_envvar_cache: dict[str, Any] | None = None


class Account(NamedTuple):
    username: str
    account_id: str


def _load_envvar_json() -> dict[str, Any]:
    global _envvar_cache
    if _envvar_cache is not None:
        return _envvar_cache

    for path in ENVVAR_PATHS:
        if not path.exists():
            continue
        try:
            _envvar_cache = json.loads(path.read_text())
            for key, value in _envvar_cache.items():
                os.environ.setdefault(key, str(value))
            return _envvar_cache
        except (OSError, json.JSONDecodeError):
            continue

    _envvar_cache = {}
    return _envvar_cache


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is not None:
        return value
    value = _load_envvar_json().get(name)
    return str(value) if value is not None else default


def auth_mode() -> str:
    return (_env("MICROSOFT_MCP_AUTH_MODE", "h4_user_profile") or "").lower()


def using_h4_user_profile() -> bool:
    return auth_mode() in H4_AUTH_MODES


def _get_h4_profile():
    agent_cli_path = pl.Path(
        _env("MICROSOFT_MCP_AGENT_CLI_PATH", str(DEFAULT_AGENT_CLI_PATH)) or ""
    ).expanduser()
    if str(agent_cli_path) not in sys.path:
        sys.path.insert(0, str(agent_cli_path))

    try:
        from accessor_user_profile import UserProfile
    except ImportError as exc:
        raise RuntimeError(
            f"Cannot import accessor_user_profile from {agent_cli_path}"
        ) from exc

    user_id = _env("H4APIUSER_ID")
    api_key = _env("H4APIAPI_KEY")
    missing = [
        name
        for name, value in (("H4APIUSER_ID", user_id), ("H4APIAPI_KEY", api_key))
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing H4 UserProfile configuration: " + ", ".join(missing)
        )

    return UserProfile.initialize(user_id=user_id, api_key=api_key)


def assert_configured() -> None:
    if using_h4_user_profile():
        _get_h4_profile()
        return
    if not _env("MICROSOFT_MCP_CLIENT_ID"):
        raise ValueError("MICROSOFT_MCP_CLIENT_ID environment variable is required")


def _h4_account() -> Account:
    profile = _get_h4_profile()
    username = (
        profile.properties.get("email")
        or profile.properties.get("Email")
        or profile.properties.get("office365_user_id")
        or profile.user_id
        or "h4-user-profile"
    )
    return Account(username=str(username), account_id="default")


def _get_h4_user_profile_token() -> str:
    profile = _get_h4_profile()
    token = profile.current_access_token
    if not token:
        raise RuntimeError(
            "No Office 365 access token found in MongoDB for this H4 user profile"
        )
    return token


def _read_cache() -> str | None:
    try:
        return CACHE_FILE.read_text()
    except FileNotFoundError:
        return None


def _write_cache(content: str) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(content)


def get_app() -> msal.PublicClientApplication:
    client_id = _env("MICROSOFT_MCP_CLIENT_ID")
    if not client_id:
        raise ValueError("MICROSOFT_MCP_CLIENT_ID environment variable is required")

    tenant_id = _env("MICROSOFT_MCP_TENANT_ID", "common")
    authority = f"https://login.microsoftonline.com/{tenant_id}"

    cache = msal.SerializableTokenCache()
    cache_content = _read_cache()
    if cache_content:
        cache.deserialize(cache_content)

    app = msal.PublicClientApplication(
        client_id, authority=authority, token_cache=cache
    )

    return app


def get_token(account_id: str | None = None) -> str:
    if using_h4_user_profile():
        return _get_h4_user_profile_token()

    app = get_app()

    accounts = app.get_accounts()

    def _find_account() -> dict | None:
        if not accounts:
            return None
        if account_id is None or account_id.strip().lower() in {"", "default", "me", "primary"}:
            return accounts[0]
        for a in accounts:
            if a.get("home_account_id") == account_id:
                return a
        for a in accounts:
            if a.get("username", "").lower() == account_id.lower():
                return a
        return None

    account = _find_account()

    result = app.acquire_token_silent(SCOPES, account=account) if account else None

    if not result:
        if accounts and account_id and account is None:
            available = ", ".join(
                f"{a.get('username')} ({a.get('home_account_id')})" for a in accounts
            )
            raise Exception(
                f"Account '{account_id}' not found. Available accounts: {available}. "
                "Pass 'default' to use the first account."
            )
        flow = app.initiate_device_flow(scopes=SCOPES)
        if "user_code" not in flow:
            raise Exception(
                f"Failed to get device code: {flow.get('error_description', 'Unknown error')}"
            )
        verification_uri = flow.get(
            "verification_uri",
            flow.get("verification_url", "https://microsoft.com/devicelogin"),
        )
        print(
            f"\nTo authenticate:\n1. Visit {verification_uri}\n2. Enter code: {flow['user_code']}"
        )
        result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        raise Exception(
            f"Auth failed: {result.get('error_description', result['error'])}"
        )

    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())

    return result["access_token"]


def list_accounts() -> list[Account]:
    if using_h4_user_profile():
        return [_h4_account()]

    app = get_app()
    return [
        Account(username=a["username"], account_id=a["home_account_id"])
        for a in app.get_accounts()
    ]


def authenticate_new_account() -> Account | None:
    if using_h4_user_profile():
        return _h4_account()

    """Authenticate a new account interactively"""
    app = get_app()

    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise Exception(
            f"Failed to get device code: {flow.get('error_description', 'Unknown error')}"
        )

    print("\nTo authenticate:")
    print(
        f"1. Visit: {flow.get('verification_uri', flow.get('verification_url', 'https://microsoft.com/devicelogin'))}"
    )
    print(f"2. Enter code: {flow['user_code']}")
    print("3. Sign in with your Microsoft account")
    print("\nWaiting for authentication...")

    result = app.acquire_token_by_device_flow(flow)

    if "error" in result:
        raise Exception(
            f"Auth failed: {result.get('error_description', result['error'])}"
        )

    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())

    # Get the newly added account
    accounts = app.get_accounts()
    if accounts:
        # Find the account that matches the token we just got
        for account in accounts:
            if (
                account.get("username", "").lower()
                == result.get("id_token_claims", {})
                .get("preferred_username", "")
                .lower()
            ):
                return Account(
                    username=account["username"], account_id=account["home_account_id"]
                )
        # If exact match not found, return the last account
        account = accounts[-1]
        return Account(
            username=account["username"], account_id=account["home_account_id"]
        )

    return None


def refresh_token(account_id: str) -> dict[str, str]:
    """Refresh access token for a specific account"""
    if using_h4_user_profile():
        _get_h4_user_profile_token()
        return {
            "status": "success",
            "message": "H4 UserProfile token is available from MongoDB",
            "token_type": "Bearer",
        }

    app = get_app()
    
    # Find the account
    accounts = app.get_accounts()
    account = None
    for acc in accounts:
        if acc["home_account_id"] == account_id:
            account = acc
            break
    
    if not account:
        raise ValueError(f"Account {account_id} not found")
    
    # Try to get token silently (refresh if needed)
    result = app.acquire_token_silent(SCOPES, account=account)
    
    if "error" in result:
        raise Exception(f"Token refresh failed: {result.get('error_description', result['error'])}")
    
    # Save updated cache
    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())
    
    return {
        "status": "success",
        "message": "Token refreshed successfully",
        "expires_in": result.get("expires_in", 0),
        "token_type": result.get("token_type", "Bearer")
    }


def logout_account(account_id: str) -> dict[str, str]:
    """Logout and remove a specific account from the cache"""
    if using_h4_user_profile():
        return {
            "status": "error",
            "message": "Logout is not supported for H4 UserProfile MongoDB token auth",
        }

    app = get_app()
    
    # Find the account
    accounts = app.get_accounts()
    account = None
    for acc in accounts:
        if acc["home_account_id"] == account_id:
            account = acc
            break
    
    if not account:
        return {"status": "error", "message": f"Account {account_id} not found"}
    
    # Remove the account from cache
    app.remove_account(account)
    
    # Save updated cache
    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())
    
    return {
        "status": "success",
        "message": f"Account {account['username']} logged out successfully"
    }


def get_auth_status() -> dict[str, Any]:
    """Get authentication status for all accounts"""
    if using_h4_user_profile():
        account = _h4_account()
        token_available = bool(_get_h4_user_profile_token())
        return {
            "status": "success",
            "auth_mode": "h4_user_profile",
            "total_accounts": 1,
            "authenticated_accounts": 1 if token_available else 0,
            "accounts": [
                {
                    "account_id": account.account_id,
                    "username": account.username,
                    "authenticated": token_available,
                }
            ],
        }

    app = get_app()
    accounts = app.get_accounts()
    
    account_statuses = []
    for account in accounts:
        # Check if we can get a token silently (indicates valid refresh token)
        result = app.acquire_token_silent(SCOPES, account=account)
        
        status = {
            "account_id": account["home_account_id"],
            "username": account["username"],
            "authenticated": "error" not in result,
        }
        
        if "error" not in result:
            status["expires_in"] = result.get("expires_in", 0)
            status["token_type"] = result.get("token_type", "Bearer")
        else:
            status["error"] = result.get("error_description", result.get("error"))
        
        account_statuses.append(status)
    
    return {
        "status": "success",
        "total_accounts": len(accounts),
        "authenticated_accounts": len([s for s in account_statuses if s["authenticated"]]),
        "accounts": account_statuses
    }


def authenticate_account() -> dict[str, Any]:
    """Start device flow authentication for new account"""
    if using_h4_user_profile():
        account = _h4_account()
        return {
            "status": "success",
            "auth_mode": "h4_user_profile",
            "message": "Using existing MongoDB-backed H4 UserProfile Office 365 token",
            "account": {
                "username": account.username,
                "account_id": account.account_id,
            },
        }

    app = get_app()
    
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        return {
            "status": "error",
            "message": f"Failed to get device code: {flow.get('error_description', 'Unknown error')}"
        }
    
    return {
        "status": "success",
        "verification_uri": flow.get("verification_uri", flow.get("verification_url", "https://microsoft.com/devicelogin")),
        "user_code": flow["user_code"],
        "expires_in": flow.get("expires_in", 900),
        "message": "Visit the verification URI and enter the user code to complete authentication",
        "flow_cache": app.token_cache.serialize() if hasattr(app.token_cache, 'serialize') else "{}"
    }


def complete_authentication(flow_cache: str) -> dict[str, Any]:
    """Complete authentication using cached flow data"""
    if using_h4_user_profile():
        account = _h4_account()
        return {
            "status": "success",
            "auth_mode": "h4_user_profile",
            "message": "No device-flow completion needed for H4 UserProfile auth",
            "account": {
                "username": account.username,
                "account_id": account.account_id,
            },
        }

    app = get_app()
    
    # Restore cache state
    if isinstance(app.token_cache, msal.SerializableTokenCache):
        app.token_cache.deserialize(flow_cache)
    
    # Get accounts to see if authentication completed
    accounts = app.get_accounts()
    
    if not accounts:
        return {
            "status": "error",
            "message": "Authentication not completed or timed out"
        }
    
    # Find the newest account (most recently authenticated)
    newest_account = accounts[-1]
    
    # Save cache
    cache = app.token_cache
    if isinstance(cache, msal.SerializableTokenCache) and cache.has_state_changed:
        _write_cache(cache.serialize())
    
    return {
        "status": "success",
        "message": "Authentication completed successfully",
        "account": {
            "username": newest_account["username"],
            "account_id": newest_account["home_account_id"]
        }
    }
