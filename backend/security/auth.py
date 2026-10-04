"""JWT authentication + scope-based authorization.

Env vars:
  JWT_SECRET   - long random string (required)
  API_CLIENTS  - JSON: {"client_id": {"secret": "...", "scopes": ["query","ingest","admin"]}}
"""
import hmac
import json
import os
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel, Field

ALGORITHM = "HS256"
TOKEN_TTL_SECONDS = 3600
VALID_SCOPES = {"query", "ingest", "admin"}

router = APIRouter(tags=["auth"])
_bearer = HTTPBearer(auto_error=False)


class TokenRequest(BaseModel):
    client_id: str = Field(..., min_length=1, max_length=100)
    client_secret: str = Field(..., min_length=1, max_length=500)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = TOKEN_TTL_SECONDS


def _secret() -> str:
    s = os.getenv("JWT_SECRET")
    if not s or len(s) < 16:
        raise RuntimeError("JWT_SECRET env var must be set (>=16 chars)")
    return s


def _clients() -> dict:
    try:
        return json.loads(os.getenv("API_CLIENTS", "{}"))
    except json.JSONDecodeError:
        return {}


def create_access_token(client_id: str, scopes: list[str]) -> str:
    payload = {
        "sub": client_id,
        "scopes": [s for s in scopes if s in VALID_SCOPES],
        "exp": int(time.time()) + TOKEN_TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM)


@router.post("/auth/token", response_model=TokenResponse)
def issue_token(body: TokenRequest):
    client = _clients().get(body.client_id)
    # constant-time compare; always compare something to avoid timing leaks
    expected = (client or {}).get("secret", "")
    ok = hmac.compare_digest(body.client_secret.encode(), expected.encode()) and client is not None
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid client credentials",
        )
    token = create_access_token(body.client_id, client.get("scopes", []))
    return TokenResponse(access_token=token)


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """Validate Bearer JWT and return the client profile."""
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if creds is None or creds.scheme.lower() != "bearer":
        raise unauthorized
    try:
        payload = jwt.decode(creds.credentials, _secret(), algorithms=[ALGORITHM])
    except JWTError:
        raise unauthorized
    client_id = payload.get("sub")
    if not client_id:
        raise unauthorized
    return {"client_id": client_id, "scopes": payload.get("scopes", [])}


def require_scope(scope: str):
    """Dependency factory: `Depends(require_scope("ingest"))`."""

    def checker(user: dict = Depends(get_current_user)) -> dict:
        if scope not in user["scopes"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {scope}",
            )
        return user

    return checker
