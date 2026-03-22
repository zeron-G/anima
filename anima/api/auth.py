"""Authentication — JWT token generation and validation."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from aiohttp import web

from anima.config import get
from anima.utils.logging import get_logger

log = get_logger("api.auth")

# Simple HMAC-SHA256 JWT (no external dependency)
_SECRET = ""

def _get_secret() -> str:
    global _SECRET
    if not _SECRET:
        _SECRET = get("dashboard.auth.token", "") or "anima-default-secret"
    return _SECRET

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
    password = get("dashboard.auth.password", "")
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

    password = get("dashboard.auth.password", "")
    if not password:
        # Auth disabled, return token anyway
        result = generate_token()
        return web.json_response(result)

    if data.get("password") != password:
        return web.json_response({"error": "invalid password"}, status=401)

    result = generate_token()
    return web.json_response(result)

async def handle_change_password(request: web.Request) -> web.Response:
    """POST /v1/auth/change-password."""
    if not check_auth(request):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        data = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    # For now, password changes require config file edit
    return web.json_response({"error": "password change requires editing local/env.yaml"}, status=501)
