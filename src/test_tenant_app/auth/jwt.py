"""JWT authentication.

AUTH_MODE=dev  — bypass; inject fixed tenant_id from DEV_TENANT_ID env var.
AUTH_MODE=live — validate Cognito JWT via JWKS (PyJWT PyJWKClient);
                 extract `tenantId` (camelCase per PRD-005 §4.1 REQ-S707),
                 map to internal snake_case `tenant_id`.
"""
from __future__ import annotations

import os
from typing import Optional

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


async def get_tenant_id(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> str:
    if AUTH_MODE == "dev":
        return DEV_TENANT_ID

    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

    token = credentials.credentials
    try:
        signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            options={"verify_aud": False},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc))

    tenant_id = claims.get("tenantId")  # camelCase per PRD-005 §4.1 REQ-S707
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Missing tenantId claim")
    return tenant_id
