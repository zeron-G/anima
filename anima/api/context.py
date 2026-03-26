"""Shared aiohttp application context keys."""

from __future__ import annotations

from typing import Any

from aiohttp import web

HUB_APP_KEY: web.AppKey[Any] = web.AppKey("hub", Any)


def get_hub(request: web.Request):
    return request.app[HUB_APP_KEY]
