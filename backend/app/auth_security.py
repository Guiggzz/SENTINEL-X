"""SENTINEL-X — auth session + Bearer API_TOKEN + rate limiting (module à brancher)."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
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
SESSION_TTL = int(os.getenv("SESSION_TTL", str(60 * 60 * 4)))  # 4 h par défaut

# Anti brute-force : 5 échecs / 15 min par IP => verrouillage 15 min (par IP, pas par compte,
# pour qu'un attaquant ne puisse pas bloquer l'opérateur pendant la démo).
LOGIN_MAX_FAILS = int(os.getenv("LOGIN_MAX_FAILS", "5"))
LOGIN_FAIL_WINDOW = 15 * 60
LOGIN_LOCK_S = int(os.getenv("LOGIN_LOCK_S", str(15 * 60)))

security_log = logging.getLogger("sentinel.security")

_login_hits: dict[str, list[float]] = {}
_cmd_hits: dict[str, list[float]] = {}
_login_fails: dict[str, list[float]] = {}
_login_locked: dict[str, float] = {}
_revoked: dict[str, float] = {}  # jeton -> expiration (déconnexion = révocation côté serveur)

# Proxys de confiance (Caddy dans un réseau Docker) : seuls eux peuvent fixer X-Forwarded-For
_TRUSTED_PROXIES = [ipaddress.ip_network(n) for n in ("172.16.0.0/12", "127.0.0.0/8", "::1/128")]


def client_ip(request: Request) -> str:
    """IP réelle du client : X-Forwarded-For (dernier saut) uniquement si la connexion vient du proxy."""
    peer = request.client.host if request.client else "unknown"
    try:
        trusted = any(ipaddress.ip_address(peer) in n for n in _TRUSTED_PROXIES)
    except ValueError:
        trusted = False
    xff = request.headers.get("x-forwarded-for", "")
    if trusted and xff:
        cand = xff.split(",")[-1].strip()
        try:
            return str(ipaddress.ip_address(cand))
        except ValueError:
            return peer
    return peer


def _safe(value: str, n: int = 32) -> str:
    """Valeur loggable (anti injection de logs)."""
    return re.sub(r"[^A-Za-z0-9_.@:-]", "?", (value or "")[:n])


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
    if token in _revoked:
        return None
    try:
        user, exp_s = payload.split(":")[:2]
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
    # nonce aléatoire : deux sessions n'ont jamais le même jeton (révocation individuelle)
    token = _sign(f"{username}:{exp}:{secrets.token_urlsafe(12)}")
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=SESSION_TTL,
        path="/",
    )


def clear_session_cookie(response: Response, token: Optional[str] = None) -> None:
    if token:
        now = time.time()
        for t, e in list(_revoked.items()):
            if e < now:
                _revoked.pop(t, None)
        _revoked[token] = now + SESSION_TTL
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


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
    ip = client_ip(request)
    now = time.time()
    until = _login_locked.get(ip, 0)
    if until > now:
        security_log.warning("LOGIN_BLOCKED ip=%s remaining=%ds", ip, int(until - now))
        raise HTTPException(status_code=429, detail="Trop de tentatives, réessayez plus tard.")
    _rate(_login_hits, ip, limit=10, window=60.0)


def login_failed(request: Request, username: str) -> None:
    ip = client_ip(request)
    now = time.time()
    fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_FAIL_WINDOW]
    fails.append(now)
    _login_fails[ip] = fails
    security_log.warning("LOGIN_FAIL ip=%s user=%s fails=%d", ip, _safe(username), len(fails))
    if len(fails) >= LOGIN_MAX_FAILS:
        _login_locked[ip] = now + LOGIN_LOCK_S
        _login_fails.pop(ip, None)
        security_log.warning("LOGIN_LOCKOUT ip=%s duration=%ds", ip, LOGIN_LOCK_S)


def login_succeeded(request: Request, username: str) -> None:
    ip = client_ip(request)
    _login_fails.pop(ip, None)
    security_log.info("LOGIN_OK ip=%s user=%s", ip, _safe(username))


def rate_limit_commands(request: Request) -> None:
    _rate(_cmd_hits, client_ip(request), limit=30, window=60.0)
