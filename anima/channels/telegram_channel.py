"""Telegram channel adapter — follows the Discord daemon-thread pattern.

Requires: pip install python-telegram-bot
Configure: channels.telegram.token in local/env.yaml or TELEGRAM_BOT_TOKEN env var
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from typing import Any, Callable

from anima.channels.base import BaseChannel
from anima.utils.logging import get_logger

log = get_logger("channels.telegram")


class TelegramChannel(BaseChannel):
    """Telegram bot channel using python-telegram-bot library.

    Architecture (mirrors DiscordChannel):
    - python-telegram-bot runs in a daemon thread with its own event loop
    - Incoming messages are put on a thread-safe queue
    - ANIMA's main loop polls this queue
    - Outgoing responses are sent via asyncio.run_coroutine_threadsafe()
    """

    def __init__(
        self,
        token: str = "",
        allowed_users: list[int] | None = None,
    ) -> None:
        super().__init__("telegram")
        self._token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._allowed_users = set(allowed_users or [])
        self._connected = False
        self._thread: threading.Thread | None = None
        self._tg_loop: asyncio.AbstractEventLoop | None = None
        self._inbox: queue.Queue = queue.Queue()
        self._response_targets: dict[str, int] = {}  # session_id -> chat_id
        self._app: Any = None  # telegram.ext.Application

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if not self._token:
            log.warning("Telegram token not configured, skipping")
            return

        import importlib.util
        if importlib.util.find_spec("telegram") is None:
            log.warning("python-telegram-bot not installed — pip install python-telegram-bot")
            return

        self._thread = threading.Thread(
            target=self._run_bot, daemon=True, name="telegram-bot",
        )
        self._thread.start()

        # Start polling inbox in ANIMA's main event loop
        asyncio.create_task(self._poll_inbox())
        log.info("Telegram channel started")

    async def stop(self) -> None:
        self._connected = False
        if self._app and self._tg_loop:
            try:
                asyncio.run_coroutine_threadsafe(
                    self._app.stop(), self._tg_loop,
                ).result(timeout=5)
            except Exception as e:
                log.debug("Telegram stop: %s", e)
        log.info("Telegram channel stopped")

    async def send(self, target: str, text: str) -> bool:
        """Send a message to a Telegram chat. Thread-safe."""
        chat_id = self._response_targets.get(target)
        if not chat_id or not self._tg_loop or not self._app:
            return False
        try:
            # Telegram message limit: 4096 chars — split at 4000 for safety
            chunks = [text[i:i + 4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                future = asyncio.run_coroutine_threadsafe(
                    self._app.bot.send_message(chat_id=chat_id, text=chunk),
                    self._tg_loop,
                )
                future.result(timeout=10)
            return True
        except Exception as e:
            log.warning("Telegram send failed: %s", e)
            return False

    def _run_bot(self) -> None:
        """Run the Telegram bot in a daemon thread with its own event loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._tg_loop = loop

        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters

            app = ApplicationBuilder().token(self._token).build()
            self._app = app

            async def on_message(update, context):
                msg = update.message
                if not msg or not msg.text:
                    return
                user_id = msg.from_user.id
                if self._allowed_users and user_id not in self._allowed_users:
                    return

                session_id = f"telegram:{user_id}"
                self._response_targets[session_id] = msg.chat_id

                self._inbox.put({
                    "text": msg.text,
                    "user": str(user_id),
                    "user_name": msg.from_user.username or str(user_id),
                    "channel": "telegram",
                    "chat_id": str(msg.chat_id),
                    "source": session_id,
                })

            app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, on_message),
            )

            self._connected = True
            log.info("Telegram bot connected (polling)")
            loop.run_until_complete(app.run_polling(drop_pending_updates=True))
        except Exception as e:
            log.error("Telegram bot error: %s", e, exc_info=True)
            self._connected = False
        finally:
            log.info("Telegram thread exited (connected=%s)", self._connected)

    async def _poll_inbox(self) -> None:
        """Poll the thread-safe inbox and forward to ANIMA's handler."""
        while True:
            try:
                data = self._inbox.get_nowait()
                if self._on_message:
                    await self._on_message(data)
                    log.info(
                        "Telegram message forwarded to ANIMA: %s",
                        data.get("text", "")[:50],
                    )
            except queue.Empty:
                pass
            except Exception as e:
                log.error("Telegram poll error: %s", e)
            await asyncio.sleep(0.3)
