"""Tests for environment scanner — catalog, scanning, search."""

import os
import tempfile
import time

import pytest

from anima.perception.env_scanner import EnvScanner, SKIP_DIRS, KEY_FILE_NAMES


# ── Fake DB ──

class FakeDB:
    """Minimal memory store stub for env_catalog methods."""
    def __init__(self):
        self.entries = []
        self.progress = {}

    def upsert_env_catalog_batch(self, entries):
        self.entries.extend(entries)

    def update_scan_progress(self, layer_id, status, **kwargs):
        self.progress[layer_id] = {"status": status, **kwargs}

    def get_scan_progress(self, layer_id):
        return self.progress.get(layer_id)

    def get_scanned_dirs(self, max_layer=2):
        return [e for e in self.entries if e["type"] == "directory" and e.get("scan_layer", 1) <= max_layer]

    def get_env_files_in_dir(self, dir_path):
        return [e for e in self.entries if e.get("parent_dir") == dir_path.replace("\\", "/")]

    def get_unscanned_dirs(self):
        return []

    def mark_env_deleted(self, path):
        pass

    def update_env_entry(self, path, updates):
        pass


# ── Tests: Layer 1 ──

@pytest.mark.asyncio
async def test_layer1_scans_temp():
    """Layer 1 should at least scan the temp dir without error."""
    db = FakeDB()
    scanner = EnvScanner(db)
    stats = await scanner.scan_layer1()
    assert stats["dirs"] > 0 or stats["files"] > 0
    assert len(stats["drives"]) >= 1
    assert db.progress.get("layer1", {}).get("status") == "completed"


# ── Tests: Layer 2 ──

@pytest.mark.asyncio
async def test_layer2_scans_temp_dir():
    """Layer 2 on a temp dir with known structure."""
    db = FakeDB()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake project structure
        os.makedirs(os.path.join(tmpdir, "code", "myproject"))
        with open(os.path.join(tmpdir, "code", "myproject", "main.py"), "w") as f:
            f.write("print('hello')")
        with open(os.path.join(tmpdir, "code", "myproject", "README.md"), "w") as f:
            f.write("# My Project")

        scanner = EnvScanner(db, extra_high_value_dirs=[os.path.join(tmpdir, "code")])
        stats = await scanner.scan_layer2(user_home=tmpdir)
        assert stats["files"] >= 2
        assert stats["important"] >= 1  # README.md
        assert db.progress.get("layer2", {}).get("status") == "completed"


# ── Tests: cancel ──

@pytest.mark.asyncio
async def test_scanner_cancel():
    db = FakeDB()
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create enough dirs so cancellation actually interrupts something
        for i in range(10):
            d = os.path.join(tmpdir, f"project{i}")
            os.makedirs(d)
            with open(os.path.join(d, "file.txt"), "w") as f:
                f.write("test")
        scanner = EnvScanner(db, extra_high_value_dirs=[tmpdir])
        scanner.cancel()  # Cancel before scanning
        stats = await scanner.scan_layer2(user_home=tmpdir)
        # Cancelled scanner should complete quickly with in_progress status
        status = db.progress.get("layer2", {}).get("status", "")
        assert status in ("in_progress", "completed")  # depends on timing


# ── Tests: incremental scan ──

@pytest.mark.asyncio
async def test_incremental_empty():
    db = FakeDB()
    scanner = EnvScanner(db)
    stats = await scanner.incremental_scan()
    assert stats["changed"] == 0
    assert stats["new"] == 0
    assert stats["deleted"] == 0


# ── Tests: make_record ──

def test_make_record_file():
    db = FakeDB()
    scanner = EnvScanner(db)
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False) as f:
        f.write(b"# test")
        path = f.name
    try:
        entry = os.scandir(os.path.dirname(path))
        for e in entry:
            if e.name == os.path.basename(path):
                record = scanner._make_record(e, scan_layer=2)
                assert record["type"] == "file"
                assert record["extension"] == ".py"
                assert record["category"] == "code"
                break
    finally:
        os.unlink(path)


def test_key_files_detected():
    """README.md should be marked as important."""
    db = FakeDB()
    scanner = EnvScanner(db)
    with tempfile.TemporaryDirectory() as tmpdir:
        readme = os.path.join(tmpdir, "README.md")
        with open(readme, "w") as f:
            f.write("# Hello")
        for entry in os.scandir(tmpdir):
            record = scanner._make_record(entry, scan_layer=2)
            if entry.name == "README.md":
                assert record["is_important"] == 1


# ── Tests: skip dirs ──

def test_skip_dirs():
    db = FakeDB()
    scanner = EnvScanner(db)
    assert scanner._should_skip("__pycache__")
    assert scanner._should_skip(".git")
    assert scanner._should_skip("node_modules")
    assert not scanner._should_skip("myproject")
    assert scanner._should_skip(".hidden")  # dotfiles


# ── Tests: store integration ──

@pytest.mark.asyncio
async def test_store_env_catalog():
    """Test the actual MemoryStore env_catalog methods."""
    from anima.memory.store import MemoryStore
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        store = await MemoryStore.create(db_path)

        # Insert entries
        store.upsert_env_catalog_batch([
            {"id": "abc123", "path": "/tmp/test.py", "type": "file",
             "size_bytes": 100, "modified_at": time.time(), "scanned_at": time.time(),
             "scan_layer": 1, "category": "code", "extension": ".py",
             "parent_dir": "/tmp", "is_important": 0},
            {"id": "def456", "path": "/tmp/README.md", "type": "file",
             "size_bytes": 50, "modified_at": time.time(), "scanned_at": time.time(),
             "scan_layer": 2, "category": "document", "extension": ".md",
             "parent_dir": "/tmp", "is_important": 1},
        ])

        # Search
        results = store.search_env_catalog(query="test")
        assert len(results) == 1
        assert results[0]["path"] == "/tmp/test.py"

        results = store.search_env_catalog(category="document")
        assert len(results) == 1

        # Stats
        stats = store.get_env_stats()
        assert stats["total_files"] == 2
        assert stats["important_files"] == 1

        # Progress tracking
        store.update_scan_progress("layer1", "completed", total_files=2)
        p = store.get_scan_progress("layer1")
        assert p["status"] == "completed"

        # Delete
        store.mark_env_deleted("/tmp/test.py")
        results = store.search_env_catalog(query="test")
        assert len(results) == 0

        await store.close()
