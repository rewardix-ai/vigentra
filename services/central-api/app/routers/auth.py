"""Demo authentication.

Module 1 scope: enough identity to make the role model and the audit trail real.
Accounts come from environment configuration; Phase 2 swaps this router for the
department identity provider without touching anything downstream.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import DemoUser, Settings
from ..database import get_db
from ..dependencies import CurrentUser, SettingsDep, authenticate, client_ip, create_access_token
from ..schemas import LoginRequest, TokenResponse, UserOut
from ..services import audit_service
from ..services.audit_service import AuditAction, AuditOutcome, ResourceType

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _to_user_out(user: DemoUser, settings: Settings) -> UserOut:
    return UserOut(
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        department=user.department,
        unit=user.unit,
        permissions=sorted(user.permissions),
        visibility_level=user.visibility,
        # Whether this ROLE may hold footage at all - not whether it may watch
        # any particular camera. That is decided per camera and per request in
        # video_permissions.evaluate, and surfaces as `video_access` on the
        # camera record itself.
        sentinel_video_access=settings.role_grants_video(user.role),
    )


@router.post("/login", response_model=TokenResponse, summary="Exchange demo credentials for a token")
async def login(
    payload: LoginRequest,
    request: Request,
    settings: SettingsDep,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user = authenticate(payload.username, payload.password, settings)

    if user is None:
        # Failed attempts are audited too - they are the ones worth keeping.
        await audit_service.record(
            db,
            username=(payload.username or "unknown")[:64],
            role="unknown",
            action=AuditAction.LOGIN_FAILED,
            outcome=AuditOutcome.DENIED,
            resource_type=ResourceType.SESSION,
            client_ip=client_ip(request),
            details={"reason": "invalid_credentials"},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, ttl = create_access_token(user, settings)
    await audit_service.record(
        db,
        username=user.username,
        role=user.role,
        action=AuditAction.LOGIN,
        resource_type=ResourceType.SESSION,
        department=user.department,
        client_ip=client_ip(request),
        details={"visibility_level": user.visibility, "permissions": sorted(user.permissions)},
    )
    return TokenResponse(access_token=token, expires_in=ttl, user=_to_user_out(user, settings))


@router.get("/me", response_model=UserOut, summary="Describe the signed-in account")
async def me(user: CurrentUser, settings: SettingsDep) -> UserOut:
    return _to_user_out(user, settings)
