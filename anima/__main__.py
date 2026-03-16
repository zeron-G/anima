"""Entry point for `python -m anima`.

Usage:
    python -m anima                  # Desktop app (PyWebView native window)
    python -m anima --headless       # Backend only (browser access)
    python -m anima --legacy         # Legacy terminal mode
    python -m anima --experimental   # Allow second instance
    python -m anima watchdog         # Watchdog mode
    python -m anima spawn user@host  # Deploy to remote
"""

import asyncio
import os
import sys

# ═══ GLOBAL ENCODING FIX ═══
# Windows uses GBK/cp936 by default. Eva's personality contains emoji (🩰💗✨)
# which crash GBK encoding. Fix ALL I/O channels at the earliest entry point.
#
# This must happen BEFORE any imports that might write to stdout/stderr.
#
# Three layers of defense:
#   1. PYTHONIOENCODING env var — affects subprocess children
#   2. sys.stdout/stderr reconfigure — affects this process
#   3. _locale coerce — affects C library defaults
os.environ["PYTHONIOENCODING"] = "utf-8"
os.environ["PYTHONUTF8"] = "1"  # Python UTF-8 mode (PEP 540)

if sys.platform == "win32":
    # Reconfigure stdout/stderr to UTF-8 with error replacement
    for stream in [sys.stdout, sys.stderr]:
        if stream and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main():
    args = sys.argv[1:]

    if args and args[0] == "watchdog":
        from anima.watchdog import run_watchdog
        run_watchdog(dry_run="--dry" in args)
    elif args and args[0] == "spawn":
        _handle_spawn(args[1:])
    elif "--watch" in args:
        _run_with_watch()
    elif "--legacy" in args:
        from anima.main import main_entry
        main_entry()
    else:
        experimental = "--experimental" in args
        headless = "--headless" in args
        from anima.desktop.app import launch_desktop
        launch_desktop(headless=headless, experimental=experimental)


def _run_with_watch():
    import subprocess
    import time
    from pathlib import Path

    root = Path(__file__).parent.parent
    src_dir = root / "anima"

    def get_mtimes():
        mtimes = {}
        for f in src_dir.rglob("*.py"):
            try:
                mtimes[str(f)] = f.stat().st_mtime
            except OSError:
                pass
        return mtimes

    print("[watch] Starting ANIMA with hot-reload...")
    while True:
        old_mtimes = get_mtimes()
        proc = subprocess.Popen([sys.executable, "-m", "anima", "--legacy"], cwd=str(root))

        try:
            while proc.poll() is None:
                time.sleep(2)
                new_mtimes = get_mtimes()
                changed = [f for f in new_mtimes if new_mtimes.get(f) != old_mtimes.get(f)]
                if changed:
                    print(f"\n[watch] Files changed: {[Path(f).name for f in changed[:5]]}")
                    print("[watch] Restarting ANIMA...")
                    proc.terminate()
                    proc.wait(timeout=10)
                    break
                old_mtimes = new_mtimes
        except KeyboardInterrupt:
            proc.terminate()
            proc.wait(timeout=5)
            print("\n[watch] Stopped.")
            return

        if proc.returncode is not None and proc.returncode != 0:
            print(f"[watch] ANIMA exited with code {proc.returncode}, restarting in 3s...")
            time.sleep(3)


def _handle_spawn(args: list[str]):
    import asyncio

    if not args or "--help" in args:
        print("Usage:")
        print("  python -m anima spawn user@host       Deploy via SSH")
        print("  python -m anima spawn --local /path    Deploy locally")
        print("  python -m anima spawn --pack-only      Create package only")
        return

    python_cmd = "python3"
    secret = ""
    include_env = True
    local_path = ""
    pack_only = False
    target = ""

    i = 0
    while i < len(args):
        if args[i] == "--python" and i + 1 < len(args):
            python_cmd = args[i + 1]; i += 2
        elif args[i] == "--secret" and i + 1 < len(args):
            secret = args[i + 1]; i += 2
        elif args[i] == "--no-env":
            include_env = False; i += 1
        elif args[i] == "--local" and i + 1 < len(args):
            local_path = args[i + 1]; i += 2
        elif args[i] == "--pack-only":
            pack_only = True; i += 1
        elif not args[i].startswith("-"):
            target = args[i]; i += 1
        else:
            i += 1

    if pack_only:
        from anima.spawn.packager import create_spawn_package
        path = create_spawn_package(network_secret=secret, include_env=include_env)
        print(f"Package created: {path}")
        return

    if local_path:
        from anima.spawn.deployer import deploy_local
        result = asyncio.run(deploy_local(local_path, network_secret=secret))
        print(f"Result: {result}")
        return

    if target:
        from anima.spawn.deployer import deploy_to_remote
        result = asyncio.run(deploy_to_remote(
            target, python_cmd=python_cmd,
            network_secret=secret, include_env=include_env,
        ))
        print(f"Result: {result}")
        return

    print("Error: specify a target (user@host), --local /path, or --pack-only")


main()
