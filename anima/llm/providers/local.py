"""Local LLM server lifecycle management (on-demand start/stop)."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

import httpx

from anima.llm.providers.constants import _LOCAL_LLM_BASE
from anima.utils.logging import get_logger

log = get_logger("llm_providers")


class LocalServerManager:
    """Manages llama-server lifecycle -- starts on demand, stops after idle.

    Config via environment variables:
        LOCAL_LLM_SERVER_CMD: full command (default: auto-detect from LOCAL_LLM_SERVER_PATH)
        LOCAL_LLM_SERVER_PATH: path to llama-server executable
        LOCAL_LLM_MODEL_PATH: path to .gguf model file
        LOCAL_LLM_BASE_URL: server URL (default: http://localhost:8080)
        LOCAL_LLM_GPU_LAYERS: number of GPU layers (default: 99 = all)
        LOCAL_LLM_CTX_SIZE: context size (default: 65536)
        LOCAL_LLM_IDLE_TIMEOUT: seconds before auto-shutdown (default: 300)
    """

    def __init__(self):
        self._process: Any = None
        self._last_used: float = 0
        self._starting: bool = False
        self._port: int = 8080

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _get_server_cmd(self) -> list[str] | None:
        """Build llama-server command from env vars."""
        # Option 1: explicit full command
        full_cmd = os.environ.get("LOCAL_LLM_SERVER_CMD", "")
        if full_cmd:
            return full_cmd.split()

        # Option 2: build from path + model
        server = os.environ.get("LOCAL_LLM_SERVER_PATH", "")
        model = os.environ.get("LOCAL_LLM_MODEL_PATH", "")
        if not server or not model:
            return None

        ngl = os.environ.get("LOCAL_LLM_GPU_LAYERS", "99")
        ctx = os.environ.get("LOCAL_LLM_CTX_SIZE", "65536")
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        # Extract port from base URL
        try:
            from urllib.parse import urlparse
            self._port = urlparse(base).port or 8080
        except Exception:
            self._port = 8080

        return [
            server, "-m", model,
            "-ngl", ngl,
            "--port", str(self._port),
            "-c", ctx,
        ]

    async def ensure_running(self, timeout: float = 30) -> bool:
        """Start server if not running. Returns True if server is ready."""
        if self.is_running:
            self._last_used = time.time()
            return True

        # Check if server is already running externally (e.g. started by evolution/user)
        base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get(f"{base}/health")
                if r.status_code == 200:
                    self._last_used = time.time()
                    self._externally_managed = True
                    log.info("Local LLM server already running externally")
                    return True
        except Exception:
            pass
        self._externally_managed = False

        if self._starting:
            # Another call is starting it -- wait
            for _ in range(int(timeout * 2)):
                await asyncio.sleep(0.5)
                if self.is_running:
                    self._last_used = time.time()
                    return True
            return False

        cmd = self._get_server_cmd()
        if not cmd:
            return False

        self._starting = True
        try:
            import subprocess
            log.info("Starting local LLM server: %s", " ".join(cmd[:3]) + "...")
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

            # Wait for server to be ready
            base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
            start = time.time()
            while time.time() - start < timeout:
                try:
                    async with httpx.AsyncClient(timeout=2) as c:
                        r = await c.get(f"{base}/health")
                        if r.status_code == 200:
                            self._last_used = time.time()
                            log.info("Local LLM server ready (%.1fs)", time.time() - start)
                            return True
                except Exception:
                    pass
                await asyncio.sleep(1)

            log.warning("Local LLM server failed to start within %ds", timeout)
            self.stop()
            return False
        finally:
            self._starting = False

    def stop(self):
        """Stop the local server."""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            log.info("Local LLM server stopped")
            self._process = None

    def check_idle_shutdown(self):
        """Stop server if idle for too long. Called periodically.

        Handles both self-started and externally-started servers.
        """
        if self._last_used == 0:
            return  # never used

        idle_timeout = int(os.environ.get("LOCAL_LLM_IDLE_TIMEOUT", "300"))
        if time.time() - self._last_used <= idle_timeout:
            return  # still within active window

        # Self-started: terminate process
        if self.is_running:
            log.info("Local LLM idle for %ds -- shutting down", idle_timeout)
            self.stop()
            return

        # Externally-started: kill by port if still running
        if getattr(self, "_externally_managed", False):
            try:
                import subprocess
                base = os.environ.get("LOCAL_LLM_BASE_URL", _LOCAL_LLM_BASE)
                with httpx.Client(timeout=2) as client:
                    r = client.get(f"{base}/health")
                if r.status_code == 200:
                    # Find and kill the process on our port
                    result = subprocess.run(
                        ["netstat", "-ano"], capture_output=True, text=True, timeout=5
                    )
                    for line in result.stdout.split("\n"):
                        if f":{self._port}" in line and "LISTENING" in line:
                            pid = line.strip().split()[-1]
                            subprocess.run(["taskkill", "/F", "/PID", pid],
                                           capture_output=True, timeout=5)
                            log.info("Killed externally-managed llama-server PID %s (idle %ds)", pid, idle_timeout)
                            self._externally_managed = False
                            break
            except Exception as e:
                log.debug("External server idle check: %s", e)

    def mark_used(self):
        self._last_used = time.time()


# Singleton
_local_server = LocalServerManager()


def get_local_server_manager() -> LocalServerManager:
    """Get the singleton local server manager (for idle shutdown checks)."""
    return _local_server
