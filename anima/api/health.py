"""Public liveness + version endpoints — /v1/health, /v1/version (no auth).

These are the only /v1 routes outside the auth middleware's protection: a
standalone frontend, load balancer, or k8s probe needs an unauthenticated way
to tell "backend up" from "auth required", and to read the server/contract
version before login (version negotiation for split deployments).
"""

from __future__ import annotations

from aiohttp import web

from anima.api.responses import ok

# Contract version for the /v1 surface. Bump on a breaking API change so a
# split-deployed frontend can detect drift against its expected version.
API_CONTRACT_VERSION = 1


def _server_version() -> str:
    try:
        from importlib.metadata import version

        return version("anima")
    except Exception:
        return "0.0.0"


async def health(request: web.Request) -> web.Response:
    """GET /v1/health — unauthenticated liveness probe."""
    return ok({"status": "ok"})


async def version(request: web.Request) -> web.Response:
    """GET /v1/version — unauthenticated server + contract version."""
    return ok({"server": _server_version(), "api": API_CONTRACT_VERSION})
