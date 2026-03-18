"""User activity detection — keyboard/mouse idle + message idle.

Windows: GetLastInputInfo API.
Linux: xprintidle or /proc/interrupts fallback.
"""

from __future__ import annotations

import platform
import time

from anima.utils.logging import get_logger

log = get_logger("user_activity")


class UserActivityDetector:
    """Detect user presence via system-level input idle and ANIMA message timing."""

    def __init__(self, use_system_api: bool = True):
        self._last_message_time: float = 0.0
        self._is_windows = platform.system() == "Windows"
        self._use_system_api = use_system_api
        self._last_input_lib = None
        if self._is_windows and use_system_api:
            try:
                import ctypes
                self._last_input_lib = ctypes
            except Exception:
                pass

    def get_system_idle_seconds(self) -> float:
        """Seconds since last keyboard/mouse input (OS-level)."""
        if not self._use_system_api:
            return self.get_message_idle_seconds()
        if self._is_windows:
            return self._win32_idle_seconds()
        return self._linux_idle_seconds()

    def _win32_idle_seconds(self) -> float:
        """Windows: GetLastInputInfo."""
        try:
            import ctypes

            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_uint),
                    ("dwTime", ctypes.c_uint),
                ]

            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            if ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii)):
                millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
                return millis / 1000.0
        except Exception as e:
            log.debug("GetLastInputInfo failed: %s", e)
        return 0.0

    def _linux_idle_seconds(self) -> float:
        """Linux: xprintidle (requires xprintidle package)."""
        try:
            import subprocess
            result = subprocess.run(
                ["xprintidle"], capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0:
                return int(result.stdout.strip()) / 1000.0
        except Exception as e:
            log.debug("_linux_idle_seconds: %s", e)
        return 0.0

    def record_user_message(self) -> None:
        """Record that the user just sent a message."""
        self._last_message_time = time.time()

    def get_message_idle_seconds(self) -> float:
        """Seconds since last user message to ANIMA."""
        if self._last_message_time == 0:
            return float("inf")
        return time.time() - self._last_message_time

    def compute_user_idle_score(self) -> float:
        """User idle score (0.0-1.0) — measures how free Eva is to work.

        Primary signal: time since last message to Eva (msg_idle).
        The user playing games / browsing / coding is NOT "busy with Eva".
        Eva should only hold back when the user is actively chatting with her.

        System input idle is only used as a small bonus: if BOTH the user
        isn't talking AND not touching keyboard, Eva is even more free.

        Scale:
        - Just chatted (< 2 min)    → 0.0-0.2 (respond mode, hold back)
        - Stepped away (2-10 min)   → 0.3-0.7 (light/moderate tasks ok)
        - Doing other things (> 10) → 0.8-1.0 (full autonomy)
        """
        msg_idle = self.get_message_idle_seconds()

        if msg_idle < 120:           # < 2 min — user is chatting
            score = 0.1 * (msg_idle / 120)
        elif msg_idle < 600:         # 2-10 min — user stepped away
            score = 0.3 + 0.4 * ((msg_idle - 120) / 480)
        else:                        # > 10 min — user doing their thing
            score = min(1.0, 0.8 + 0.2 * ((msg_idle - 600) / 1800))

        # Bonus: if system input is also idle (AFK), boost confidence slightly
        sys_idle = self.get_system_idle_seconds()
        if sys_idle > 300:  # 5+ min no keyboard/mouse
            score = min(1.0, score + 0.1)

        return round(score, 3)
