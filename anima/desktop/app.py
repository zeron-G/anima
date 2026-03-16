"""ANIMA Desktop App — PyWebView native window + backend."""

from __future__ import annotations

import asyncio
import atexit
import os
import sys
import threading


def launch_desktop(*, headless: bool = False, experimental: bool = False) -> None:
    from anima.config import get
    from anima.desktop.singleton import acquire_lock, release_lock

    if not acquire_lock(experimental=experimental):
        try:
            import webview
            webview.create_window("ANIMA", html="<h2>ANIMA is already running.</h2>", width=400, height=200)
            webview.start()
        except Exception:
            pass
        sys.exit(1)

    atexit.register(release_lock)

    port = get("dashboard.port", 8420)
    url = f"http://127.0.0.1:{port}/desktop"

    if headless:
        from anima.main import main_entry
        main_entry()
        return

    # Start backend in background thread (with restart loop)
    backend_ready = threading.Event()
    backend_thread = threading.Thread(
        target=_run_backend_loop,
        args=(backend_ready,),
        daemon=True,
        name="anima-backend",
    )
    backend_thread.start()
    backend_ready.wait(timeout=30)

    try:
        import webview
    except ImportError:
        import webbrowser
        webbrowser.open(url)
        try:
            backend_thread.join()
        except KeyboardInterrupt:
            pass
        return

    webview.create_window(
        title="ANIMA",
        url=url,
        width=1400,
        height=900,
        min_size=(1024, 680),
        background_color="#06080c",
        text_select=False,
    )

    webview.start(gui="edgechromium", debug=False)

    # Window closed — kill everything
    release_lock()
    os._exit(0)


def _run_backend_loop(ready_event: threading.Event) -> None:
    """Run the backend with restart loop (same as main_entry but in a thread).

    When evolution triggers hot-reload, run() returns True.
    We sleep briefly, then re-run — the window stays open.
    """
    # Prevent GBK encoding crashes on Windows with emoji in logs
    os.environ["PYTHONIOENCODING"] = "utf-8"

    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    import time
    from anima.utils.logging import get_logger
    log = get_logger("desktop")

    restart_count = 0
    first_start = True

    while True:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            restart = loop.run_until_complete(_backend_main(ready_event if first_start else None))
            first_start = False
        except (KeyboardInterrupt, SystemExit):
            break
        except Exception as e:
            log.error("Backend crashed: %s — restarting in 3s", e)
            restart = True  # Auto-restart on crash, don't give up
        finally:
            loop.close()

        if not restart:
            break

        # Evolution restart — brief pause then restart backend
        restart_count += 1
        log.info("Evolution restart #%d — reloading backend in 2s...", restart_count)
        time.sleep(2)


async def _backend_main(ready_event: threading.Event | None) -> bool:
    """Run the ANIMA backend once. Returns True if restart requested."""
    from anima.dashboard import server as srv_mod

    if ready_event:
        _original_start = srv_mod.DashboardServer.start

        async def _patched_start(self):
            await _original_start(self)
            ready_event.set()

        srv_mod.DashboardServer.start = _patched_start

    try:
        from anima.main import run
        return await run()
    finally:
        if ready_event:
            srv_mod.DashboardServer.start = _original_start
