"""Webhook channel — receive messages via HTTP POST."""

from __future__ import annotations
from aiohttp import web

from anima.channels.base import BaseChannel
from anima.utils.logging import get_logger

log = get_logger("channels.webhook")


class WebhookChannel(BaseChannel):
    """HTTP webhook channel.

    Receives POST /webhook with JSON body {"text": "...", "user": "...", "channel": "..."}
    Pushes to ANIMA's event queue via on_message handler.
    """

    def __init__(self, port: int = 9421, secret: str = ""):
        super().__init__("webhook")
        self._port = port
        self._secret = secret
        self._app = web.Application()
        self._app.router.add_post("/webhook", self._handle_webhook)
        self._app.router.add_get("/webhook/health", self._handle_health)
        self._runner: web.AppRunner | None = None
        self._connected = False

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "0.0.0.0", self._port)
        await site.start()
        self._connected = True
        log.info("Webhook channel started on port %d", self._port)

    async def stop(self) -> None:
        self._connected = False
        if self._runner:
            await self._runner.cleanup()
        log.info("Webhook channel stopped")

    async def send(self, target: str, text: str) -> bool:
        # Webhooks are receive-only; response is sent inline in the handler
        return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        # Optional secret check
        if self._secret:
            auth = request.headers.get("Authorization", "")
            if auth != f"Bearer {self._secret}":
                return web.json_response({"error": "unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"error": "empty text"}, status=400)

        user = data.get("user", "webhook")

        if self._on_message:
            await self._on_message({
                "text": text,
                "user": user,
                "channel": "webhook",
                "source": f"webhook:{user}",
            })

        return web.json_response({"ok": True, "received": text[:100]})

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "channel": "webhook"})
