"""Entry point for `python -m anima`.

Usage:
    python -m anima                  # Desktop app (PyWebView native window)
    python -m anima --headless       # Backend only (browser access)
    python -m anima --edge           # Edge ANIMA for robot-dog Linux nodes
    python -m anima --legacy         # Legacy terminal mode
    python -m anima --experimental   # Allow second instance
    python -m anima --profile NAME   # Load a committed runtime profile
    python -m anima watchdog         # Watchdog mode
    python -m anima spawn user@host  # Deploy to remote
    python -m anima spawn --node X   # Deploy to configured remote_nodes target
"""

import asyncio
import os
import sys

try:
    from anima.utils.logging import get_logger
    log = get_logger("__main__")
except Exception:
    import logging
    log = logging.getLogger("__main__")

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

# Patch subprocess to always use UTF-8 when text=True
# Prevents GBK decode crashes in _readerthread on Windows
import subprocess as _subprocess
_orig_run = _subprocess.run
def _utf8_run(*args, **kwargs):
    if kwargs.get("text") and "encoding" not in kwargs:
        kwargs["encoding"] = "utf-8"
        kwargs.setdefault("errors", "replace")
    return _orig_run(*args, **kwargs)
_subprocess.run = _utf8_run

# Patch subprocess.Popen to suppress console window flashing on Windows.
# Without CREATE_NO_WINDOW, every subprocess call (git, claude, gh, shell, etc.)
# briefly flashes a console window — extremely disruptive during normal use.
# Patching Popen covers BOTH direct Popen() and subprocess.run() (which uses Popen).
if sys.platform == "win32":
    _orig_Popen_init = _subprocess.Popen.__init__
    def _no_window_popen_init(self, *args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = _subprocess.CREATE_NO_WINDOW
        return _orig_Popen_init(self, *args, **kwargs)
    _subprocess.Popen.__init__ = _no_window_popen_init

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
    args, profile = _extract_option(args, "--profile")
    edge_mode = "--edge" in args
    if edge_mode:
        args = [arg for arg in args if arg != "--edge"]
        profile = profile or "edge-pidog"

    if profile:
        os.environ["ANIMA_PROFILE"] = profile

    if args and args[0] == "watchdog":
        from anima.watchdog import run_watchdog
        run_watchdog(dry_run="--dry" in args)
    elif args and args[0] == "spawn":
        _handle_spawn(args[1:], default_profile=profile, edge_mode=edge_mode)
    elif "--watch" in args:
        _run_with_watch()
    elif "--legacy" in args or edge_mode:
        from anima.main import main_entry
        main_entry()
    else:
        experimental = "--experimental" in args
        headless = "--headless" in args
        from anima.desktop.app import launch_desktop
        launch_desktop(headless=headless, experimental=experimental)


def _extract_option(args: list[str], option: str) -> tuple[list[str], str]:
    """Remove a `--flag value` option pair and return the remaining args."""
    items = list(args)
    value = ""
    if option in items:
        idx = items.index(option)
        if idx + 1 < len(items):
            value = items[idx + 1]
            del items[idx:idx + 2]
        else:
            del items[idx]
    return items, value


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
            except OSError as e:
                log.debug("get_mtimes: %s", e)
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


def _handle_spawn(args: list[str], *, default_profile: str = "", edge_mode: bool = False):
    import asyncio

    if not args or "--help" in args:
        print("Usage:")
        print("  python -m anima spawn user@host                      Deploy via SSH")
        print("  python -m anima spawn --node <name>                  Deploy to configured remote_nodes target")
        print("  python -m anima spawn user@host --edge               Deploy edge PiDog profile")
        print("  python -m anima spawn --local /path                  Deploy locally")
        print("  python -m anima spawn --pack-only [--edge]           Create package only")
        print("  python -m anima spawn --profile edge-pidog --pack-only")
        print("  Flags: --with-env to include .env, --no-env to force omit .env")
        return

    python_cmd = "python3"
    secret = ""
    include_env: bool | None = None
    local_path = ""
    install_dir = ""
    profile = default_profile
    pack_only = False
    install_service: bool | None = None
    target = ""
    known_node = ""
    resolved_edge_mode: bool | None = True if edge_mode else None

    i = 0
    while i < len(args):
        if args[i] == "--python" and i + 1 < len(args):
            python_cmd = args[i + 1]; i += 2
        elif args[i] == "--secret" and i + 1 < len(args):
            secret = args[i + 1]; i += 2
        elif args[i] == "--profile" and i + 1 < len(args):
            profile = args[i + 1]; i += 2
        elif args[i] == "--no-env":
            include_env = False; i += 1
        elif args[i] == "--with-env":
            include_env = True; i += 1
        elif args[i] == "--node" and i + 1 < len(args):
            known_node = args[i + 1]; i += 2
        elif args[i] == "--local" and i + 1 < len(args):
            local_path = args[i + 1]; i += 2
        elif args[i] == "--install-dir" and i + 1 < len(args):
            install_dir = args[i + 1]; i += 2
        elif args[i] == "--pack-only":
            pack_only = True; i += 1
        elif args[i] == "--install-service":
            install_service = True; i += 1
        elif args[i] == "--edge":
            resolved_edge_mode = True
            profile = profile or "edge-pidog"
            i += 1
        elif not args[i].startswith("-"):
            target = args[i]; i += 1
        else:
            i += 1

    from anima.spawn.targets import resolve_deploy_plan

    plan = resolve_deploy_plan(
        profile=profile,
        edge_mode=resolved_edge_mode,
        install_service=install_service,
        install_dir=install_dir,
        python_cmd=python_cmd,
        include_env=include_env,
    )

    if pack_only:
        from anima.spawn.packager import create_spawn_package
        path = create_spawn_package(
            network_secret=secret,
            include_env=plan["include_env"],
            profile=plan["profile"],
            edge_mode=plan["edge_mode"],
            install_service=plan["install_service"],
        )
        print(f"Package created: {path}")
        return

    if local_path:
        from anima.spawn.deployer import deploy_local
        result = asyncio.run(
            deploy_local(
                local_path,
                network_secret=secret,
                profile=plan["profile"],
                edge_mode=plan["edge_mode"],
            )
        )
        print(f"Result: {result}")
        return

    if known_node:
        from anima.spawn.deployer import deploy_to_known_node

        result = asyncio.run(deploy_to_known_node(
            known_node,
            python_cmd=python_cmd,
            install_dir=install_dir,
            network_secret=secret,
            include_env=include_env,
            profile=profile,
            edge_mode=resolved_edge_mode,
            install_service=install_service,
        ))
        print(f"Result: {result}")
        return

    if target:
        from anima.spawn.deployer import deploy_to_remote

        result = asyncio.run(deploy_to_remote(
            target,
            python_cmd=python_cmd,
            install_dir=install_dir,
            network_secret=secret, include_env=include_env,
            profile=profile,
            edge_mode=resolved_edge_mode,
            install_service=install_service,
        ))
        print(f"Result: {result}")
        return

    print("Error: specify a target (user@host), --node <name>, --local /path, or --pack-only")


main()
