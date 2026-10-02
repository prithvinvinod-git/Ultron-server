"""Authentication routes (T021, spec §30).

Five endpoints: login, refresh, logout, whoami, password change. That is the
whole surface -- token issuance and revocation are the only authentication
operations that need to be reachable over HTTP, and each one is auditable on
its own.

Two decisions worth stating.

**The routes use no administrator bypass.** There is no path here that skips the
check, because the alternative is a flag that some future route forgets to set.
``require_superuser`` composes the same dependencies any other route would use.

**Tokens are returned exactly once and never logged.** They are plaintext here
and only their digests exist in the database, so a token in a log file or a
proxy capture is as good as one in the response. The route bodies never log
their inputs; the access log records method, path, and status only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import (
    Auth,
    Authenticator,
    client_ip,
    get_authenticator,
    get_user_repository,
    user_agent,
)
from app.core.errors import InvalidInputError, NotFoundError
from app.database.repositories import UserRepository
from app.security import tokens

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    """A username and password.

    ``extra="forbid"`` so an unrecognised field is a 422 rather than being
    silently dropped: a client sending the wrong field name should find out
    immediately, not after wondering why its extra argument did nothing.
    """

    model_config = ConfigDict(extra="forbid")

    username: Annotated[str, Field(min_length=1, max_length=255)]
    password: Annotated[str, Field(min_length=1, max_length=1024)]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: Annotated[str, Field(min_length=1, max_length=512)]


class ChangePasswordRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: Annotated[str, Field(min_length=1, max_length=1024)]
    new_password: Annotated[str, Field(min_length=12, max_length=1024)]


class TokenResponse(BaseModel):
    """A freshly issued token pair.

    ``session_id`` is included so a client can correlate the token it just
    received with the session row in the audit trail; it is not a secret.
    """

    access_token: str
    refresh_token: str
    # The literal RFC 6750 scheme name, not a credential.
    token_type: str = "bearer"  # noqa: S105
    expires_at: str
    refresh_expires_at: str | None
    session_id: str


class PrincipalResponse(BaseModel):
    actor_type: str
    username: str | None
    is_superuser: bool


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange a username and password for a session",
)
async def login(
    payload: LoginRequest,
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
) -> TokenResponse:
    """Authenticate with a password and start a session.

    Every failure returns the same 401 with the same message, whether the
    username is unknown, the password is wrong, or the account is disabled.
    """
    issued = await authenticator.login(
        payload.username,
        payload.password,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_at=issued.expires_at.isoformat(),
        refresh_expires_at=(
            issued.refresh_expires_at.isoformat() if issued.refresh_expires_at else None
        ),
        session_id=str(issued.session_id),
    )


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotate a refresh token",
)
async def refresh(
    payload: RefreshRequest,
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
) -> TokenResponse:
    """Exchange a refresh token for a new pair, invalidating the old one.

    Presenting a token that was already rotated revokes the whole session
    family: at that point the holder cannot be distinguished from whoever
    replayed it.
    """
    issued = await authenticator.refresh(
        payload.refresh_token,
        ip_address=client_ip(request),
        user_agent=user_agent(request),
    )
    return TokenResponse(
        access_token=issued.access_token,
        refresh_token=issued.refresh_token,
        expires_at=issued.expires_at.isoformat(),
        refresh_expires_at=(
            issued.refresh_expires_at.isoformat() if issued.refresh_expires_at else None
        ),
        session_id=str(issued.session_id),
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the current session",
)
async def logout(
    request: Request,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
) -> None:
    """Revoke the session behind the presented token.

    Deliberately unauthenticated and idempotent: this has to work for a caller
    whose token has already expired, since that is exactly the moment someone is
    most likely to reach for logout. The authenticator decides whether there is
    anything to revoke.

    A missing or malformed header is therefore not an error here. Parsing it with
    :func:`extract_bearer` and propagating the ``AuthError`` would return 401 to
    the one client that most needs to be able to log out, and would make this the
    only endpoint whose failure mode depends on whether the caller is already
    logged in. Nothing is revoked in that case, which is the correct outcome for
    a credential that cannot be identified.
    """
    header = request.headers.get("authorization")
    credential = tokens.extract_bearer(header) if header else None
    if credential is not None:
        await authenticator.logout(credential, ip_address=client_ip(request))


@router.get(
    "/me",
    response_model=PrincipalResponse,
    summary="Describe the authenticated caller",
)
async def me(principal: Auth) -> PrincipalResponse:
    """Return who the caller is, for a client deciding what to render."""
    return PrincipalResponse(
        actor_type=principal.actor_type,
        username=principal.username,
        is_superuser=principal.is_superuser,
    )


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the authenticated user's password",
)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    principal: Auth,
    authenticator: Annotated[Authenticator, Depends(get_authenticator)],
    users: Annotated[UserRepository, Depends(get_user_repository)],
) -> None:
    """Change a password after verifying the current one.

    Every other session for the account is revoked. A password change is the
    usual response to a suspected compromise, and leaving the old sessions
    alive would defeat the purpose of changing it.
    """
    if principal.user_id is None:
        message = "this credential has no password to change"
        raise InvalidInputError(message, details={"actor": principal.actor_type})

    user = await users.get(principal.user_id)
    if user is None:
        raise NotFoundError("that user no longer exists")

    await authenticator.change_password(
        user,
        payload.current_password,
        payload.new_password,
        ip_address=client_ip(request),
    )


__all__ = [
    "ChangePasswordRequest",
    "LoginRequest",
    "PrincipalResponse",
    "RefreshRequest",
    "TokenResponse",
    "router",
]
