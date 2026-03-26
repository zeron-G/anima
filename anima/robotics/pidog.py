"""HTTP transport for the deployed PiDog Eva service."""

from __future__ import annotations

from typing import Any

import aiohttp

from anima.robotics.models import RobotNodeConfig
from anima.utils.logging import get_logger

log = get_logger("robotics.pidog")


class PiDogApiClient:
    """Small resilient client for the PiDog Eva Layer 3 API."""

    def __init__(self, config: RobotNodeConfig, session: aiohttp.ClientSession) -> None:
        self._config = config
        self._session = session
        self._active_base_url: str = config.base_urls[0] if config.base_urls else ""

    @property
    def active_base_url(self) -> str:
        return self._active_base_url

    async def health(self) -> dict[str, Any]:
        payload, _ = await self._request("GET", "/health", timeout_s=self._config.status_timeout_s)
        return payload

    async def status(self) -> dict[str, Any]:
        payload, _ = await self._request("GET", "/status", timeout_s=self._config.status_timeout_s)
        return payload

    async def command(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload, base_url = await self._request(
            "POST",
            "/command",
            json_payload={"command": command, "params": params or {}},
            timeout_s=self._config.command_timeout_s,
        )
        if isinstance(payload, dict):
            payload.setdefault("base_url", base_url)
            payload.setdefault("command", command)
        return payload

    async def nlp(self, text: str) -> dict[str, Any]:
        payload, base_url = await self._request(
            "POST",
            "/nlp",
            json_payload={"text": text},
            timeout_s=self._config.command_timeout_s,
        )
        if isinstance(payload, dict):
            payload.setdefault("base_url", base_url)
        return payload

    async def speak(self, text: str, blocking: bool = False) -> dict[str, Any]:
        payload, base_url = await self._request(
            "POST",
            "/speak",
            json_payload={"text": text, "blocking": blocking},
            timeout_s=self._config.command_timeout_s,
        )
        if isinstance(payload, dict):
            payload.setdefault("base_url", base_url)
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        timeout_s: float,
    ) -> tuple[dict[str, Any], str]:
        urls = self._ordered_urls()
        if not urls:
            raise RuntimeError(f"Robot node '{self._config.node_id}' has no configured base_urls")

        last_error: Exception | None = None
        for base_url in urls:
            try:
                timeout = aiohttp.ClientTimeout(total=timeout_s)
                async with self._session.request(
                    method,
                    f"{base_url}{path}",
                    json=json_payload,
                    timeout=timeout,
                ) as response:
                    response.raise_for_status()
                    payload = await response.json(content_type=None)
                    self._active_base_url = base_url
                    return payload, base_url
            except Exception as exc:  # pragma: no cover - exercised via manager tests
                last_error = exc
                log.debug("PiDog request failed (%s%s): %s", base_url, path, exc)

        raise RuntimeError(f"PiDog request {method} {path} failed on all endpoints: {last_error}")

    def _ordered_urls(self) -> list[str]:
        urls = list(self._config.base_urls)
        if self._active_base_url and self._active_base_url in urls:
            urls.remove(self._active_base_url)
            urls.insert(0, self._active_base_url)
        elif self._active_base_url:
            urls.insert(0, self._active_base_url)
        return urls
