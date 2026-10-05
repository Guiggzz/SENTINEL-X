"""SENTINEL-X — auth session + Bearer API_TOKEN + rate limiting (module à brancher)."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from typing import Callable, Optional

from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

try:
    import bcrypt
except ImportError:  # fallback soft
    bcrypt = None

OPERATOR_USER = os.getenv("OPERATOR_USER", "operateur")
OPERATOR_PASSWORD_HASH = os.getenv("OPERATOR_PASSWORD_HASH", "")
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
API_TOKEN = os.getenv("API_TOKEN", "")
SESSION_COOKIE = "sentinel_session"
SESSION_TTL = 60 * 60 * 8  # 8 h

_login_hits: dict[str, list[float]] = {}
_cmd_hits: dict[str, list[float]] = {}


def _rate(bucket: dict[str, list[float]], key: str, limit: int, window: float) -> None:
    now = time.time()
    hits = [t for t in bucket.get(key, []) if now - t < window]
    if len(hits) >= limit:
        raise HTTPException(status_code=429, detail="Trop de requêtes, réessayez plus tard.")
    hits.append(now)
    bucket[key] = hits


def hash_password(password: str) -> str:
    if bcrypt is None:
        raise RuntimeError("bcrypt requis")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash or bcrypt is None:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except Exception:
        return False


def _sign(payload: str) -> str:
    if not SESSION_SECRET:
        raise RuntimeError("SESSION_SECRET manquant")
    sig = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _unsign(token: str) -> Optional[str]:
    if not token or "." not in token or not SESSION_SECRET:
        return None
    payload, sig = token.rsplit(".", 1)
    expect = hmac.new(SESSION_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expect):
        return None
    try:
        user, exp_s = payload.split(":", 1)
        if int(exp_s) < int(time.time()):
            return None
        return user
    except Exception:
        return None


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


def create_session_cookie(response: Response, username: str) -> None:
    exp = int(time.time()) + SESSION_TTL
    token = _sign(f"{username}:{exp}")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=SESSION_TTL,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


def session_user(sentinel_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE)) -> Optional[str]:
    return _unsign(sentinel_session) if sentinel_session else None


def require_auth(
    request: Request,
    sentinel_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE),
    authorization: Optional[str] = Header(default=None),
) -> str:
    # Bearer machine token
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        if API_TOKEN and hmac.compare_digest(token, API_TOKEN):
            return "api-token"
    user = _unsign(sentinel_session) if sentinel_session else None
    if user:
        return user
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentification requise")


def rate_limit_login(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _rate(_login_hits, ip, limit=8, window=60.0)


def rate_limit_commands(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    _rate(_cmd_hits, ip, limit=30, window=60.0)
