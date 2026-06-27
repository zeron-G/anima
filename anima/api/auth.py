"""Authentication — JWT token generation and validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from aiohttp import web

from anima.config import get
from anima.secret_store import resolve as _resolve_secret
from anima.utils.logging import get_logger

log = get_logger("api.auth")

# Simple HMAC-SHA256 JWT (no external dependency)
_SECRET = ""

def _get_secret() -> str:
    global _SECRET
    if not _SECRET:
        configured = _resolve_secret(get("dashboard.auth.token", ""))
        if configured:
            _SECRET = configured
        else:
            _SECRET = secrets.token_hex(32)
            log.warning(
                "No dashboard.auth.token configured — using random per-process secret. "
                "Tokens will not survive restarts. Set 'dashboard.auth.token' in local/env.yaml."
            )
    return _SECRET

_login_attempts: dict[str, list[float]] = {}
_MAX_LOGIN_ATTEMPTS = 5
_LOGIN_WINDOW_S = 60.0

def generate_token(ttl_hours: int = 24) -> dict:
    """Generate a JWT-like token."""
    secret = _get_secret()
    payload = {
        "iat": int(time.time()),
        "exp": int(time.time()) + ttl_hours * 3600,
    }
    import base64
    header = base64.urlsafe_b64encode(json.dumps({"alg": "HS256"}).encode()).decode().rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()[:32]
    token = f"{header}.{body}.{sig}"
    return {"token": token, "expires_at": payload["exp"]}

def verify_token(token: str) -> bool:
    """Verify a JWT-like token."""
    if not token:
        return False
    secret = _get_secret()
    parts = token.split(".")
    if len(parts) != 3:
        return False
    header, body, sig = parts
    expected_sig = hmac.new(secret.encode(), f"{header}.{body}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected_sig):
        return False
    # Check expiration
    import base64
    try:
        padding = "=" * (4 - len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + padding))
        if payload.get("exp", 0) < time.time():
            return False
    except Exception:
        return False
    return True

def check_auth(request: web.Request) -> bool:
    """Check if request is authenticated. Returns True if auth passes."""
    password = _resolve_secret(get("dashboard.auth.password", ""))
    if not password:
        return True  # Auth disabled when no password set

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        if verify_token(token):
            return True
    # Query param fallback (WebSocket)
    if request.query.get("token"):
        return verify_token(request.query["token"])
    return False

async def handle_login(request: web.Request) -> web.Response:
    """POST /v1/auth/login — authenticate and get token."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    # Rate limiting
    client_ip = request.remote or "unknown"
    now = time.time()
    attempts = _login_attempts.get(client_ip, [])
    # Clean old entries
    attempts = [t for t in attempts if now - t < _LOGIN_WINDOW_S]
    _login_attempts[client_ip] = attempts
    if len(attempts) >= _MAX_LOGIN_ATTEMPTS:
        return web.json_response(
            {"error": "too many login attempts, try again later"},
            status=429,
        )

    password = _resolve_secret(get("dashboard.auth.password", ""))
    if not password:
        # Auth disabled, return token anyway
        result = generate_token()
        return web.json_response(result)

    if not hmac.compare_digest(data.get("password", ""), password):
        _login_attempts.setdefault(client_ip, []).append(now)
        return web.json_response({"error": "invalid password"}, status=401)

    result = generate_token()
    return web.json_response(result)

async def handle_change_password(request: web.Request) -> web.Response:
    """POST /v1/auth/change-password."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    # For now, password changes require config file edit
    return web.json_response({"error": "password change requires editing local/env.yaml"}, status=501)
