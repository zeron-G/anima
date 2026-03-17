"""Environment deep scanner — three-layer scan + incremental detection.

Layer 1 (skeleton): All drives, top-level dirs (depth=2). ~5 seconds.
Layer 2 (high-value): Projects, Desktop, Documents — full tree. ~minutes.
Layer 3 (full disk): Everything else, chunk by chunk. ~hours to days.

Results stored in SQLite env_catalog table. After initial scan, only
incremental changes need to be detected.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from anima.utils.logging import get_logger

log = get_logger("env_scanner")

# Directories to always skip
SKIP_DIRS = {
    "$Recycle.Bin", "System Volume Information", "$WinREAgent",
    "Windows.old", "Recovery", "PerfLogs",
    "__pycache__", "node_modules", ".git", ".svn", ".hg",
    "venv", ".venv", "env",
    ".tox", ".pytest_cache", ".mypy_cache",
    "dist", "build", "target", ".gradle",
    "Temp", "tmp",
}

# High-value directory name patterns (case-insensitive matching)
HIGH_VALUE_NAMES = {
    "desktop", "桌面", "documents", "文档", "downloads", "下载",
    "code", "projects", "repos", "workspace", "dev", "data", "src",
}

# Key files that deserve LLM summaries
KEY_FILE_NAMES = {
    "README.md", "readme.md", "README.txt", "README",
    "package.json", "pyproject.toml", "setup.py", "setup.cfg",
    "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
    "Makefile", "CMakeLists.txt", "Dockerfile", "docker-compose.yml",
    ".env.example", "requirements.txt", "Pipfile",
    "config.yaml", "config.yml", "config.json", "config.toml",
    "main.py", "main.go", "main.rs", "index.js", "index.ts",
    "App.tsx", "App.vue", "CLAUDE.md",
}

# Extension → category
CATEGORY_MAP = {
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code", ".tsx": "code",
    ".go": "code", ".rs": "code", ".java": "code", ".c": "code", ".cpp": "code",
    ".h": "code", ".cs": "code", ".rb": "code", ".php": "code", ".swift": "code",
    ".kt": "code", ".lua": "code", ".sh": "code", ".ps1": "code", ".bat": "code",
    ".yaml": "config", ".yml": "config", ".json": "config", ".toml": "config",
    ".ini": "config", ".cfg": "config", ".conf": "config", ".xml": "config",
    ".md": "document", ".txt": "document", ".rst": "document",
    ".doc": "document", ".docx": "document", ".pdf": "document",
    ".jpg": "media", ".jpeg": "media", ".png": "media", ".gif": "media",
    ".svg": "media", ".bmp": "media", ".webp": "media",
    ".mp3": "media", ".wav": "media", ".flac": "media",
    ".mp4": "media", ".avi": "media", ".mkv": "media", ".mov": "media",
    ".csv": "data", ".tsv": "data", ".parquet": "data",
    ".db": "data", ".sqlite": "data", ".sql": "data",
    ".exe": "binary", ".dll": "binary", ".so": "binary", ".msi": "binary",
    ".zip": "archive", ".tar": "archive", ".gz": "archive", ".7z": "archive",
    ".rar": "archive",
}


class EnvScanner:
    """Three-layer environment scanner with incremental detection."""

    def __init__(self, db, extra_skip_dirs: list[str] | None = None,
                 extra_high_value_dirs: list[str] | None = None,
                 batch_size: int = 500):
        self._db = db  # MemoryStore (has env_catalog methods)
        self._batch_size = batch_size
        self._cancelled = False
        self._extra_skip = set(extra_skip_dirs or [])
        self._extra_high_value = list(extra_high_value_dirs or [])

    def cancel(self) -> None:
        self._cancelled = True

    # ── Layer 1: skeleton scan ──

    async def scan_layer1(self) -> dict:
        """Scan all drives, depth=2. Fast (~5s)."""
        self._cancelled = False
        stats = {"dirs": 0, "files": 0, "drives": []}

        if os.name == "nt":
            import string
            drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
        else:
            drives = ["/"]
        stats["drives"] = drives

        entries: list[dict] = []
        for drive in drives:
            self._scan_flat(drive, entries, stats, depth=2, scan_layer=1)

        if entries:
            self._db.upsert_env_catalog_batch(entries)
        self._db.update_scan_progress("layer1", "completed",
                                       total_files=stats["files"],
                                       scanned_dirs=stats["dirs"])

        log.info("Layer 1 complete: %d dirs, %d files across %s",
                 stats["dirs"], stats["files"], drives)
        return stats

    def _scan_flat(self, root: str, entries: list[dict], stats: dict,
                   depth: int, scan_layer: int, _current: int = 0) -> None:
        """Non-recursive flat scan to given depth."""
        if _current >= depth:
            return
        try:
            for entry in os.scandir(root):
                if self._should_skip(entry.name):
                    continue
                record = self._make_record(entry, scan_layer)
                entries.append(record)
                if entry.is_dir(follow_symlinks=False):
                    stats["dirs"] += 1
                    if _current + 1 < depth:
                        self._scan_flat(entry.path, entries, stats, depth,
                                        scan_layer, _current + 1)
                else:
                    stats["files"] += 1
        except (PermissionError, OSError) as e:
            log.debug("_scan_flat: %s", e)

    # ── Layer 2: high-value areas ──

    async def scan_layer2(self, user_home: str | None = None) -> dict:
        """Scan high-value directories recursively. Supports resume."""
        self._cancelled = False
        if not user_home:
            user_home = str(Path.home())

        stats = {"dirs": 0, "files": 0, "important": 0}
        high_value_dirs = self._find_high_value_dirs(user_home)

        self._db.update_scan_progress("layer2", "in_progress",
                                       total_dirs=len(high_value_dirs))

        for dir_path in high_value_dirs:
            if self._cancelled:
                break
            self._scan_recursive(dir_path, 2, stats, max_depth=10)

        status = "completed" if not self._cancelled else "in_progress"
        self._db.update_scan_progress("layer2", status,
                                       total_files=stats["files"],
                                       scanned_dirs=stats["dirs"])
        log.info("Layer 2 %s: %d dirs, %d files, %d important",
                 status, stats["dirs"], stats["files"], stats["important"])
        return stats

    def _find_high_value_dirs(self, user_home: str) -> list[str]:
        dirs = []
        home = Path(user_home)
        for item in HIGH_VALUE_NAMES:
            candidate = home / item
            if candidate.exists():
                dirs.append(str(candidate))
        for extra in self._extra_high_value:
            if os.path.exists(extra):
                dirs.append(extra)
        # Check other drives
        if os.name == "nt":
            import string
            for d in string.ascii_uppercase:
                drive = f"{d}:\\"
                if not os.path.exists(drive):
                    continue
                try:
                    for entry in os.scandir(drive):
                        if entry.is_dir() and entry.name.lower() in HIGH_VALUE_NAMES:
                            p = entry.path
                            if p not in dirs:
                                dirs.append(p)
                except (PermissionError, OSError) as e:
                    log.debug("env_scanner: %s", e)
        return dirs

    # ── Layer 3: full disk (chunked) ──

    async def scan_layer3_chunk(self, chunk_size: int = 1000) -> dict:
        """Scan a chunk of the remaining unscanned directories."""
        self._cancelled = False
        stats = {"dirs": 0, "files": 0}

        # Get layer1 dirs that haven't been scanned by layer2/3
        unscanned = self._db.get_unscanned_dirs()
        if not unscanned:
            self._db.update_scan_progress("layer3", "completed")
            log.info("Layer 3 complete — nothing left to scan")
            return stats

        self._db.update_scan_progress("layer3", "in_progress")

        total = 0
        last_dir = ""
        for dir_path in unscanned:
            if self._cancelled or total >= chunk_size:
                break
            before = stats["files"]
            self._scan_recursive(dir_path, 3, stats, max_depth=5)
            total += stats["files"] - before
            last_dir = dir_path

        self._db.update_scan_progress(
            "layer3", "in_progress",
            total_files=stats["files"], scanned_dirs=stats["dirs"],
            last_scanned_path=last_dir,
        )
        log.info("Layer 3 chunk: %d dirs, %d files", stats["dirs"], stats["files"])
        return stats

    # ── Incremental scan ──

    async def incremental_scan(self) -> dict:
        """Quick check for changes in already-scanned directories."""
        self._cancelled = False
        stats = {"changed": 0, "deleted": 0, "new": 0}

        known_dirs = self._db.get_scanned_dirs(max_layer=2)
        for dir_info in known_dirs:
            if self._cancelled:
                break
            dir_path = dir_info["path"]
            if not os.path.exists(dir_path):
                stats["deleted"] += 1
                self._db.mark_env_deleted(dir_path)
                continue

            known_files = self._db.get_env_files_in_dir(dir_path)
            known_map = {f["path"]: f for f in known_files}

            try:
                for entry in os.scandir(dir_path):
                    if self._should_skip(entry.name):
                        continue
                    path = entry.path.replace("\\", "/")
                    if path in known_map:
                        try:
                            st = entry.stat(follow_symlinks=False)
                            if abs(st.st_mtime - known_map[path].get("modified_at", 0)) > 1:
                                stats["changed"] += 1
                                self._db.update_env_entry(path, {
                                    "modified_at": st.st_mtime,
                                    "size_bytes": st.st_size if not entry.is_dir() else 0,
                                    "scanned_at": time.time(),
                                })
                        except OSError as e:
                            log.debug("env_scanner: %s", e)
                        del known_map[path]
                    else:
                        stats["new"] += 1
                        record = self._make_record(entry, scan_layer=2)
                        self._db.upsert_env_catalog_batch([record])
            except (PermissionError, OSError):
                continue

            for deleted_path in known_map:
                stats["deleted"] += 1
                self._db.mark_env_deleted(deleted_path)

        log.info("Incremental: %d changed, %d new, %d deleted",
                 stats["changed"], stats["new"], stats["deleted"])
        return stats

    # ── Internals ──

    def _scan_recursive(self, root: str, scan_layer: int, stats: dict,
                        max_depth: int = 10, _depth: int = 0) -> None:
        if _depth > max_depth or self._cancelled:
            return
        try:
            scan_entries = list(os.scandir(root))
        except (PermissionError, OSError):
            return

        batch: list[dict] = []
        for entry in scan_entries:
            if self._cancelled:
                break
            if self._should_skip(entry.name):
                continue
            record = self._make_record(entry, scan_layer)
            batch.append(record)
            if entry.is_dir(follow_symlinks=False):
                stats["dirs"] = stats.get("dirs", 0) + 1
                self._scan_recursive(entry.path, scan_layer, stats,
                                     max_depth, _depth + 1)
            else:
                stats["files"] = stats.get("files", 0) + 1
                if record.get("is_important"):
                    stats["important"] = stats.get("important", 0) + 1

            if len(batch) >= self._batch_size:
                self._db.upsert_env_catalog_batch(batch)
                batch.clear()

        if batch:
            self._db.upsert_env_catalog_batch(batch)

    def _should_skip(self, name: str) -> bool:
        if name.startswith("."):
            return True
        if name in SKIP_DIRS or name in self._extra_skip:
            return True
        return False

    def _make_record(self, entry: os.DirEntry, scan_layer: int) -> dict:
        try:
            st = entry.stat(follow_symlinks=False)
            mtime = st.st_mtime
            size = st.st_size if not entry.is_dir() else 0
        except OSError:
            mtime = 0
            size = 0

        is_dir = entry.is_dir(follow_symlinks=False)
        ext = os.path.splitext(entry.name)[1].lower() if not is_dir else ""
        category = CATEGORY_MAP.get(ext, "other") if ext else ("directory" if is_dir else "other")
        is_important = 1 if entry.name in KEY_FILE_NAMES else 0
        path = entry.path.replace("\\", "/")

        return {
            "id": hashlib.md5(path.encode()).hexdigest(),
            "path": path,
            "type": "directory" if is_dir else "file",
            "size_bytes": size,
            "modified_at": mtime,
            "scanned_at": time.time(),
            "scan_layer": scan_layer,
            "category": category,
            "extension": ext,
            "parent_dir": str(Path(entry.path).parent).replace("\\", "/"),
            "is_important": is_important,
        }
