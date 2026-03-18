"""Discord channel — Discord bot integration.

Runs discord.py in its own thread with its own event loop.
Uses thread-safe queues to communicate with ANIMA's main loop.
"""

from __future__ import annotations

import asyncio
import os
import queue
import sys
import threading

from anima.channels.base import BaseChannel
from anima.utils.logging import get_logger

log = get_logger("channels.discord")


class DiscordChannel(BaseChannel):
    """Discord bot channel.

    Architecture:
    - discord.py runs in a daemon thread with its own event loop
    - Incoming messages are put on a thread-safe queue
    - ANIMA's main loop polls this queue
    - Outgoing responses are sent via thread-safe method
    """

    def __init__(self, token: str = "", allowed_users: list[str] | None = None):
        super().__init__("discord")
        self._token = token or os.environ.get("DISCORD_BOT_TOKEN", "")
        self._allowed_users = set(allowed_users or [])
        self._client = None
        self._connected = False
        self._thread: threading.Thread | None = None
        self._discord_loop: asyncio.AbstractEventLoop | None = None

        # Thread-safe queues
        self._inbox: queue.Queue = queue.Queue()  # Discord → ANIMA
        self._response_targets: dict[str, int] = {}  # session_id → channel_id

        # Typing indicator tasks: channel_id → asyncio.Task
        self._typing_tasks: dict[int, asyncio.Task] = {}

    async def start(self) -> None:
        if not self._token:
            log.warning("Discord token not configured, skipping")
            return

        import importlib.util
        if importlib.util.find_spec("discord") is None:
            log.warning("discord.py not installed. Run: pip install discord.py")
            return

        self._thread = threading.Thread(target=self._run_in_thread, daemon=True)
        self._thread.start()

        # Start polling task in ANIMA's event loop
        asyncio.create_task(self._poll_inbox())

    async def stop(self) -> None:
        self._connected = False
        if self._discord_loop and self._client:
            asyncio.run_coroutine_threadsafe(self._client.close(), self._discord_loop)
        log.info("Discord channel stopped")

    async def send(self, target: str, text: str) -> bool:
        """Send a message to Discord. Thread-safe."""
        if not self._client or not self._connected or not self._discord_loop:
            return False

        channel_id = self._response_targets.get(target)
        if not channel_id:
            # Try all stored targets
            for sid, cid in self._response_targets.items():
                channel_id = cid
                break
        if not channel_id:
            log.warning("No Discord channel to reply to for %s", target)
            return False

        try:
            future = asyncio.run_coroutine_threadsafe(
                self._send_in_discord(channel_id, text),
                self._discord_loop,
            )
            future.result(timeout=10)
            return True
        except Exception as e:
            log.error("Discord send error: %s", e)
            return False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _run_in_thread(self) -> None:
        """Run discord.py in its own thread + event loop."""
        import discord

        # Windows needs SelectorEventLoop for discord.py too
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._discord_loop = loop

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            self._connected = True
            log.info("Discord connected as %s", self._client.user)

        @self._client.event
        async def on_message(message):
            if message.author == self._client.user:
                return
            if self._allowed_users and str(message.author.id) not in self._allowed_users:
                return

            session_id = f"discord:{message.author.id}"
            self._response_targets[session_id] = message.channel.id

            # Start persistent typing indicator immediately
            await self._start_typing(message.channel.id)

            # Put on thread-safe queue (ANIMA polls this)
            self._inbox.put({
                "text": message.content,
                "user": str(message.author.id),
                "user_name": message.author.display_name,
                "channel": "discord",
                "channel_id": str(message.channel.id),
                "guild": message.guild.name if message.guild else "DM",
                "source": session_id,
            })

        try:
            log.info("Discord thread starting with token %s...", self._token[:20])
            loop.run_until_complete(self._client.start(self._token))
        except Exception as e:
            log.error("Discord client error: %s", e, exc_info=True)
            self._connected = False
        finally:
            log.info("Discord thread exited (connected=%s)", self._connected)

    async def _start_typing(self, channel_id: int) -> None:
        """Start a persistent typing indicator loop for a channel (discord loop)."""
        # Cancel any existing typing task for this channel
        old = self._typing_tasks.get(channel_id)
        if old and not old.done():
            old.cancel()

        async def _typing_loop():
            channel = self._client.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self._client.fetch_channel(channel_id)
                except Exception:
                    return
            try:
                while True:
                    await channel.trigger_typing()
                    await asyncio.sleep(8)  # Discord typing lasts ~10s, refresh every 8s
            except asyncio.CancelledError:
                pass
            except Exception as e:
                log.debug("Typing loop ended: %s", e)

        task = asyncio.ensure_future(_typing_loop())
        self._typing_tasks[channel_id] = task

    async def _stop_typing(self, channel_id: int) -> None:
        """Cancel the typing indicator for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task and not task.done():
            task.cancel()

    async def _send_in_discord(self, channel_id: int, text: str) -> None:
        """Send message via discord client (runs in discord's event loop)."""
        channel = self._client.get_channel(channel_id)
        if channel is None:
            channel = await self._client.fetch_channel(channel_id)

        # Stop typing indicator before sending
        await self._stop_typing(channel_id)

        # Split long messages
        for i in range(0, len(text), 1900):
            await channel.send(text[i:i + 1900])

    async def _poll_inbox(self) -> None:
        """Poll the thread-safe inbox forever and forward to ANIMA's handler."""
        while True:
            try:
                data = self._inbox.get_nowait()
                if self._on_message:
                    await self._on_message(data)
                    log.info("Discord message forwarded to ANIMA: %s", data.get("text", "")[:50])
            except queue.Empty:
                pass
            except Exception as e:
                log.error("Discord poll error: %s", e)
            await asyncio.sleep(0.3)
