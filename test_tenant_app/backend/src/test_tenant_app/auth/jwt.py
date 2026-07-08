"""JWT authentication.

AUTH_MODE=dev  — bypass; inject fixed tenant_id from DEV_TENANT_ID env var.
AUTH_MODE=live — validate Cognito JWT via JWKS (PyJWT PyJWKClient);
                 extract `tenantId` (camelCase per PRD-005 §4.1 REQ-S707),
                 map to internal snake_case `tenant_id`.
"""

from __future__ import annotations

import os
from typing import Optional, TypedDict

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

AUTH_MODE = os.getenv("AUTH_MODE", "dev")
DEV_TENANT_ID = os.getenv("DEV_TENANT_ID", "6eb4ebaf-804e-5837-ae26-f665a76b58dd")
COGNITO_REGION = os.getenv("COGNITO_REGION", "us-east-1")
COGNITO_USER_POOL_ID = os.getenv("COGNITO_USER_POOL_ID", "")

_jwks_client: Optional[jwt.PyJWKClient] = None


def _get_jwks_client() -> jwt.PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        jwks_uri = (
            f"https://cognito-idp.{COGNITO_REGION}.amazonaws.com"
            f"/{COGNITO_USER_POOL_ID}/.well-known/jwks.json"
        )
        _jwks_client = jwt.PyJWKClient(jwks_uri)
    return _jwks_client


_bearer = HTTPBearer(auto_error=False)


class Approver(TypedDict):
    """Authenticated approver identity threaded to Node 6's HITL claims gate."""

    user_id: str
    tenant_id: str
    claims: dict


def _decode_claims(credentials: Optional[HTTPAuthorizationCredentials]) -> dict:
    """Validate the Cognito JWT and return its claims (live mode only)."""
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(credentials.credentials)
        return jwt.decode(
            credentials.credentials,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))


def _tenant_from_claims(claims: dict) -> str:
    tenant_id = claims.get("tenantId")  # camelCase per PRD-005 §4.1 REQ-S707
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing tenantId claim")
    return tenant_id


async def get_tenant_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if AUTH_MODE == "dev":
        return DEV_TENANT_ID
    return _tenant_from_claims(_decode_claims(credentials))


async def get_approver(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> Approver:
    """The authenticated approver — carries the JWT claims Node 6's gate checks."""
    if AUTH_MODE == "dev":
        return {"user_id": "dev-approver", "tenant_id": DEV_TENANT_ID, "claims": {}}
    claims = _decode_claims(credentials)
    return {
        "user_id": claims.get("sub", "unknown"),
        "tenant_id": _tenant_from_claims(claims),
        "claims": claims,
    }
