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
        except Exception:
            pass
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
        """User activity dimension idle score (0.0-1.0).

        Combines system-level input idle and ANIMA message idle.
        - Active (< 60s) → 0.0
        - Short absence (1-5min) → 0.1-0.5
        - Long absence (5-30min) → 0.5-0.8
        - Sleeping (> 30min) → 0.8-1.0
        """
        sys_idle = self.get_system_idle_seconds()
        msg_idle = self.get_message_idle_seconds()

        # Use the more conservative (shorter) idle signal
        effective_idle = min(sys_idle, msg_idle)

        if effective_idle < 60:
            return 0.0
        elif effective_idle < 300:       # 1-5 min
            return 0.1 + 0.4 * ((effective_idle - 60) / 240)
        elif effective_idle < 1800:      # 5-30 min
            return 0.5 + 0.3 * ((effective_idle - 300) / 1500)
        else:                            # > 30 min
            return min(1.0, 0.8 + 0.2 * ((effective_idle - 1800) / 3600))
