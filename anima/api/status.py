"""Readiness + liveness endpoints backed by the Sentinel.

- GET /v1/healthz  — public, minimal machine-to-machine liveness (no auth, so a
  process-liveness limb / probe can't be locked out by a 401). Cheap.
- GET /v1/status   — authed, rich component health snapshot. Returns 503 when
  overall health is "down" so external probes can act on readiness.

(`/v1/health` stays a static {"status":"ok"} for dumb LB/k8s liveness.)
"""
from __future__ import annotations

from aiohttp import web

from anima.api.context import get_hub


def _sentinel(request: web.Request):
    hub = get_hub(request)
    return getattr(hub, "safety_monitor", None)


async def healthz(request: web.Request) -> web.Response:
    """GET /v1/healthz — unauthenticated minimal liveness."""
    sentinel = _sentinel(request)
    return web.json_response({
        "alive": True,
        "sentinel_tick": sentinel.sentinel_tick if sentinel else None,
        "draining": False,
    })


async def status(request: web.Request) -> web.Response:
    """GET /v1/status — rich readiness snapshot (503 when overall is down)."""
    sentinel = _sentinel(request)
    if sentinel is None:
        return web.json_response(
            {"overall": "unknown", "detail": "sentinel not initialised", "components": {}},
            status=200,
        )
    snap = sentinel.snapshot()
    code = 503 if snap.get("overall") == "down" else 200
    return web.json_response(snap, status=code)
