"""Shared FastAPI dependencies: identity, permissions, adapters.

Module 1 identity is deliberately small - signed bearer tokens over a demo user
list supplied by environment. What is NOT simplified is authorisation: every
route declares the permission it needs, and every record-touching route also
checks that the account's department owns the record.

There is no media client dependency here, because there is no video path.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from secrets import compare_digest
from typing import Annotated

import httpx
import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .adapters.base import SurveillanceAdapter
from .config import DemoUser, Settings, get_settings

bearer_scheme = HTTPBearer(auto_error=False, description="Vigentra demo bearer token")

TOKEN_ISSUER = "vigentra-central-api"


def get_settings_dep() -> Settings:
    return get_settings()


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]


# --------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------

def authenticate(username: str, password: str, settings: Settings) -> DemoUser | None:
    """Verify demo credentials in constant time.

    Demo accounts are plain values held in environment variables, which is
    stated plainly in the README. Phase 2 replaces this whole function with the
    department identity provider; nothing downstream changes.
    """
    for user in settings.demo_users:
        if compare_digest(user.username, username) and compare_digest(user.password, password):
            return user
    return None


def create_access_token(user: DemoUser, settings: Settings) -> tuple[str, int]:
    """Return (token, ttl_seconds)."""
    ttl = timedelta(minutes=settings.access_token_ttl_minutes)
    now = datetime.now(timezone.utc)
    payload = {
        "iss": TOKEN_ISSUER,
        "sub": user.username,
        "name": user.display_name,
        "role": user.role,
        "dept": user.department,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm), int(
        ttl.total_seconds()
    )


async def get_current_user(
    settings: SettingsDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> DemoUser:
    """Resolve the bearer token to a live account record.

    Role and department are re-read from configuration on every request rather
    than trusted from the token, so a permission change takes effect
    immediately instead of at token expiry.
    """
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            issuer=TOKEN_ISSUER,
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired - sign in again",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    username = payload.get("sub", "")
    for user in settings.demo_users:
        if user.username == username:
            return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user")


CurrentUser = Annotated[DemoUser, Depends(get_current_user)]


def require_permission(permission: str) -> Callable[[DemoUser], DemoUser]:
    """Dependency factory: refuse the request unless the role holds `permission`."""

    def _dependency(user: CurrentUser) -> DemoUser:
        if not user.can(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Role '{user.role}' does not hold the '{permission}' permission"
                ),
            )
        return user

    return _dependency


# --------------------------------------------------------------------------
# Runtime resources
# --------------------------------------------------------------------------

def get_adapters(request: Request) -> dict[str, SurveillanceAdapter]:
    adapters = getattr(request.app.state, "adapters", None)
    if not adapters:
        raise HTTPException(status_code=503, detail="Department adapters are not initialised")
    return adapters


AdaptersDep = Annotated[dict, Depends(get_adapters)]


def get_media_client(request: Request) -> httpx.AsyncClient:
    """The client used to proxy brokered video.

    Separate from the adapters' clients so a long video read cannot starve
    control-plane calls behind a shared connection pool.
    """
    client = getattr(request.app.state, "media_client", None)
    if client is None:
        raise HTTPException(status_code=503, detail="Media client is not initialised")
    return client


MediaClientDep = Annotated[httpx.AsyncClient, Depends(get_media_client)]


def client_ip(request: Request) -> str | None:
    """Best-effort client address for the audit trail."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
