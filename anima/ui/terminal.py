"""Terminal UI — rich output + async input with clean prompt handling."""

from __future__ import annotations

import asyncio
import sys
import threading

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from anima.config import agent_name
from anima.core.event_queue import EventQueue
from anima.models.event import Event, EventType, EventPriority
from anima.utils.logging import get_logger

log = get_logger("terminal")

_PROMPT = "You> "


class TerminalUI:
    """Terminal interface with clean output.

    Solves the prompt interleaving problem:
    - input() runs in a background thread
    - display() can be called at any time from the async loop
    - Before printing output, we clear the current "You> " line
    - After printing, we re-show the prompt
    """

    def __init__(self, event_queue: EventQueue) -> None:
        self._event_queue = event_queue
        self._console = Console(highlight=False)
        self._running = False
        self._agent_name = agent_name().upper()
        self._waiting_for_input = False
        self._lock = threading.Lock()

    def display(self, text: str, style: str = "green") -> None:
        """Display agent's output to the user. Thread-safe.

        Renders markdown (bold, code blocks, lists) via rich.Markdown.
        Handles Unicode encoding errors gracefully (Windows GBK terminals
        can't display some emoji like 🩰 U+1FA70).
        """
        # Use Markdown renderer if text contains markdown indicators
        if any(c in text for c in ("**", "```", "- ", "# ")):
            content = Markdown(text)
        else:
            content = Text(text)
        panel = Panel(
            content,
            title=self._agent_name,
            border_style=style,
            padding=(0, 1),
        )
        with self._lock:
            if self._waiting_for_input:
                # Clear the "You> " prompt line before printing
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()

            try:
                self._console.print(panel)
            except UnicodeEncodeError:
                # Windows terminal can't encode some Unicode chars (emoji etc.)
                # Strip them and retry
                safe_text = text.encode("gbk", errors="replace").decode("gbk", errors="replace")
                try:
                    self._console.print(Panel(
                        Text(safe_text), title=self._agent_name,
                        border_style=style, padding=(0, 1),
                    ))
                except Exception:
                    # Last resort — plain print
                    print(f"[{self._agent_name}] {safe_text}")

            if self._waiting_for_input:
                # Restore the prompt
                sys.stdout.write(_PROMPT)
                sys.stdout.flush()

    def display_system(self, text: str) -> None:
        """Display a system message (not from agent)."""
        with self._lock:
            if self._waiting_for_input:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()

            self._console.print(f"[dim]{text}[/dim]")

            if self._waiting_for_input:
                sys.stdout.write(_PROMPT)
                sys.stdout.flush()

    async def start(self) -> None:
        """Start the input loop."""
        self._running = True
        self._console.print(
            Panel(f"{self._agent_name} is alive. Type your message and press Enter.\n"
                  f"Commands: /quit  /status",
                  border_style="blue", title="ANIMA")
        )

        while self._running:
            try:
                user_input = await asyncio.to_thread(self._blocking_input)
                if user_input is None:
                    continue
                text = user_input.strip()
                if not text:
                    continue

                # Special commands
                if text.lower() in ("/quit", "/exit", "/q"):
                    log.info("User requested quit")
                    await self._event_queue.put(Event(
                        type=EventType.SHUTDOWN,
                        priority=EventPriority.CRITICAL,
                        source="user",
                    ))
                    return

                if text.lower() == "/status":
                    self.display_system("Status check requested")
                    continue

                # Push user message as event
                await self._event_queue.put(Event(
                    type=EventType.USER_MESSAGE,
                    payload={"text": text},
                    priority=EventPriority.HIGH,
                    source="terminal",
                ))

            except asyncio.CancelledError:
                return
            except EOFError:
                return
            except ValueError as e:
                # stdin unavailable in non-interactive mode — not a real error
                log.debug("Terminal input unavailable: %s", e)
                return
            except Exception as e:
                err_str = str(e)
                # stdin lost / non-interactive — not a real error, just exit cleanly
                if "sys.stdin" in err_str or "lost" in err_str:
                    log.debug("Terminal stdin unavailable, stopping input loop: %s", e)
                    return
                log.error("Terminal input error: %s", e)
                # Don't tight-loop on repeated errors — back off briefly
                await asyncio.sleep(1.0)

    def _blocking_input(self) -> str | None:
        """Blocking input call — runs in thread."""
        try:
            self._waiting_for_input = True
            result = input(_PROMPT)
            return result
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            self._waiting_for_input = False

    def stop(self) -> None:
        """Signal the terminal to stop."""
        self._running = False
        with self._lock:
            if self._waiting_for_input:
                sys.stdout.write("\r\033[K")
                sys.stdout.flush()
            self._console.print(f"\n[dim]{self._agent_name} shutting down...[/dim]")
