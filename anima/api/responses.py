"""Standard API response envelope + request-parsing helpers (Phase 4).

Canonical envelope (the contract for /v1):
  success: {"ok": true,  "data": <payload>}
  error:   {"ok": false, "error": "<message>"}   + an HTTP status code

New and refactored handlers SHOULD use ok()/err(). read_json()/query_int()
centralize input parsing so malformed input yields a clean 400 — never an
unhandled 500. Raise ApiError from anywhere in a handler to abort with a
standardized error response (converted by the error middleware in server.py).

NOTE: many existing /v1 handlers still return their historical bare/per-resource
shapes; those are documented verbatim in docs/API.md. The envelope here is the
forward standard and is already used by /v1/health, /v1/version and error paths.
"""

from __future__ import annotations

from typing import Any

from aiohttp import web


class ApiError(Exception):
    """Raise inside a handler to abort with a clean {ok:false,error} response.

    The error middleware (server.py) converts it to err(message, status).
    """

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def ok(data: Any = None, *, status: int = 200) -> web.Response:
    """Standard success response: {"ok": true, "data": ...}."""
    return web.json_response({"ok": True, "data": data}, status=status)


def err(message: str, *, status: int = 400) -> web.Response:
    """Standard error response: {"ok": false, "error": message}."""
    return web.json_response({"ok": False, "error": message}, status=status)


async def read_json(request: web.Request) -> dict:
    """Parse a JSON object body or raise ApiError(400). Never 500 on bad input."""
    try:
        data = await request.json()
    except Exception as exc:  # JSONDecodeError, ContentTypeError, ...
        raise ApiError("invalid json", 400) from exc
    if not isinstance(data, dict):
        raise ApiError("json body must be an object", 400)
    return data


def query_int(
    request: web.Request,
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    """Parse an int query param, clamped to [minimum, maximum]; ApiError(400) on garbage."""
    raw = request.query.get(name, "")
    if raw == "":
        val = default
    else:
        try:
            val = int(raw)
        except (TypeError, ValueError) as exc:
            raise ApiError(f"invalid {name}", 400) from exc
    if minimum is not None and val < minimum:
        val = minimum
    if maximum is not None and val > maximum:
        val = maximum
    return val
