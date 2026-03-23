"""Evolution Sandbox — git worktree isolation + three-level testing.

Provides isolated development environment via git worktree,
and a three-level test framework:
  Level 1: Static checks (syntax, imports)
  Level 2: Unit + integration tests (pytest)
  Level 3: Sandbox simulation (start full ANIMA, verify health)
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from anima.config import project_root
from anima.utils.logging import get_logger
from anima.utils.path_safety import validate_path_within

log = get_logger("evolution.sandbox")

_PYTHON = sys.executable


class Worktree:
    """Git worktree for isolated development."""

    def __init__(self, proposal_id: str) -> None:
        self.proposal_id = proposal_id
        self.branch = f"evo/{proposal_id}"
        self.path: Path | None = None
        self._created = False

    def create(self) -> Path:
        """Create a git worktree for this evolution."""
        root = project_root()
        worktree_dir = Path(tempfile.gettempdir()) / f"anima-evo-{self.proposal_id}"

        try:
            # Create branch from current HEAD
            subprocess.run(
                ["git", "branch", self.branch],
                cwd=str(root), capture_output=True, check=False, timeout=15,
            )

            # Create worktree
            subprocess.run(
                ["git", "worktree", "add", str(worktree_dir), self.branch],
                cwd=str(root), capture_output=True, check=True, timeout=30,
            )

            self.path = worktree_dir
            self._created = True
            log.info("Worktree created: %s → %s", self.branch, worktree_dir)
            return worktree_dir

        except subprocess.CalledProcessError as e:
            log.error("Failed to create worktree: %s", e.stderr.decode() if e.stderr else str(e))
            raise

    def cleanup(self) -> None:
        """Remove the worktree and branch."""
        if not self._created:
            return

        root = project_root()
        try:
            subprocess.run(
                ["git", "worktree", "remove", str(self.path), "--force"],
                cwd=str(root), capture_output=True, check=False, timeout=15,
            )
            subprocess.run(
                ["git", "branch", "-D", self.branch],
                cwd=str(root), capture_output=True, check=False, timeout=15,
            )
            log.info("Worktree cleaned: %s", self.branch)
        except Exception as e:
            log.warning("Worktree cleanup failed: %s", e)

    def get_diff(self) -> str:
        """Get the diff of changes in this worktree."""
        if not self.path:
            return ""
        try:
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=str(self.path), capture_output=True, text=True,
                encoding="utf-8", errors="replace", check=True,
            )
            return result.stdout
        except Exception:
            return ""

    def commit(self, message: str) -> bool:
        """Commit changes in the worktree."""
        if not self.path:
            return False
        try:
            subprocess.run(
                ["git", "add", "-A"],
                cwd=str(self.path), capture_output=True, check=True, timeout=15,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(self.path), capture_output=True, check=True, timeout=15,
            )
            return True
        except subprocess.CalledProcessError:
            return False


class TestRunner:
    """Three-level test framework."""

    def __init__(self, working_dir: str | Path | None = None) -> None:
        self._cwd = str(working_dir or project_root())

    def _run(self, cmd: list[str], timeout: int = 60) -> tuple[bool, str]:
        try:
            result = subprocess.run(
                cmd, cwd=self._cwd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                timeout=timeout, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            output = (result.stdout + "\n" + result.stderr).strip()
            return result.returncode == 0, output
        except subprocess.TimeoutExpired:
            return False, "TIMEOUT"
        except Exception as e:
            return False, str(e)

    def level1_static(self, changed_files: list[str]) -> tuple[bool, str]:
        """Level 1: Static checks — syntax and imports."""
        if not changed_files:
            return True, "No files to check"

        errors = []
        # Use working dir as valid root (supports both main repo and worktree)
        valid_root = Path(self._cwd).resolve()
        for f in changed_files:
            if not f.endswith(".py"):
                continue
            # Resolve relative path — try as-is, then with anima/ prefix
            fpath = Path(self._cwd) / f
            # Validate path is within working directory (prevent traversal)
            try:
                validate_path_within(fpath, valid_root)
            except Exception:
                log.warning("Path traversal blocked in sandbox: %s", f)
                continue
            if not fpath.exists():
                fpath = Path(self._cwd) / "anima" / f
            if not fpath.exists():
                # Skip files that don't exist (might be new/deleted)
                continue
            ok, out = self._run([_PYTHON, "-m", "py_compile", str(fpath)], timeout=10)
            if not ok:
                errors.append(f"{f}: {out}")

        if errors:
            return False, "Static check failed:\n" + "\n".join(errors)

        log.info("Level 1 (static): PASS (%d files)", len(changed_files))
        return True, "OK"

    def level2_pytest(self) -> tuple[bool, str]:
        """Level 2: Unit + integration tests.

        Ignores known-slow and external-dependency tests to keep
        the evolution pipeline fast and reliable.
        """
        ok, out = self._run(
            [_PYTHON, "-m", "pytest", "tests/", "--tb=short", "-q",
             "-p", "no:timeout",
             "--ignore=tests/stress_test.py",
             "--ignore=tests/test_oauth_live.py",
             "--ignore=tests/test_full_system.py",
             "--ignore=tests/test_integration_network.py",
             "--ignore=tests/test_task_delegate_extended.py"],
            timeout=120,
        )
        if ok:
            log.info("Level 2 (pytest): PASS")
        else:
            log.warning("Level 2 (pytest): FAIL")
        return ok, out

    async def level3_sandbox(self, timeout: int = 30) -> tuple[bool, str]:
        """Level 3: Start ANIMA in sandbox, verify health after timeout."""
        log.info("Level 3 (sandbox): starting ANIMA instance...")

        proc = subprocess.Popen(
            [_PYTHON, "-m", "anima", "--headless"],
            cwd=self._cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            await asyncio.sleep(timeout)

            # Check if still alive
            if proc.poll() is not None:
                stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
                return False, f"Process died (exit {proc.returncode}): {stderr[:500]}"

            # Check log for errors
            log_file = Path(self._cwd) / "data" / "logs" / "anima.log"
            if log_file.exists():
                log_text = log_file.read_text(encoding="utf-8", errors="replace")
                lines = log_text.splitlines()
                errors = [line for line in lines[-30:] if "[ERROR]" in line or "[CRITICAL]" in line]
                if errors:
                    return False, "Runtime errors:\n" + "\n".join(errors[:5])

            log.info("Level 3 (sandbox): PASS")
            return True, "OK"

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
