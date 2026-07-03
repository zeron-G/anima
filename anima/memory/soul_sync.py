"""Soul snapshot + restore (P3e) — durability & cross-node convergence for the
per-node persona FILES.

Persona/soul is authoritative as FILES on each node's disk (identity/*.md, rules,
feelings, growth_log, golden_replies, lorebook) — the prompt compiler reads those
files directly and they stay vim/Soulscape-editable. This module is only the safety
net the schema NOTE promises: it snapshots changed soul files up to the shared cloud
store (``soul_snapshots``) and, on startup/(re)provision, pulls newer-or-missing
files back down — so a node whose disk is lost, or freshly provisioned from the
generic seed, recovers Eva's real soul instead of booting a stranger.

DESIGN STANCE — the soul is sacred: **never destroy or overwrite a local soul file
that could hold real, unsynced edits, and never push junk (empty/seed) to the shared
cloud.** When in doubt, keep local + journal. Concretely the ONLY local overwrites are:
  1. local file MISSING            → restore (nothing to lose);
  2. local == the generic SEED     → provision (back up seed, adopt cloud);
  3. cloud newer AND local == last-synced version (manifest hash match, i.e. no
     unsynced local edits) → safe pull.
Every other case (local edited since sync, ambiguous no-manifest divergence, an
unreadable file) KEEPS LOCAL and journals — real soul is never clobbered. Cross-node
merge of genuinely conflicting edits (decision #3, Eva-LLM semantic merge) is a
follow-up; v1 errs entirely toward preserving the local self.

A local manifest (``.soul_sync.json`` under agent_dir, written atomically) records
the last-synced {version, hash} per file — that's how we tell "local edited since
sync" from "cloud edited since sync". Cloud unreachable → both ops no-op (the local
files ARE the offline cache).
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

from anima.config import data_dir, seed_agents_dir
from anima.utils.logging import get_logger

log = get_logger("soul_sync")

# Relative globs under agent_dir that make up "the soul". Deliberately excludes
# .bak/.soulbak snapshots, the deprecated persona_state.yaml, and transient files.
SOUL_GLOBS = (
    "identity/*.md",
    "identity/*.yaml",
    "rules/*.md",
    "memory/feelings.md",
    "memory/growth_log.md",
    "memory/golden_replies.jsonl",
    "lorebook/_index.yaml",
    "lorebook/*.md",
    "examples/*.md",
    "post_processing/style_rules.yaml",
)

_MANIFEST_NAME = ".soul_sync.json"


def _hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


class SoulSync:
    def __init__(self, cloud_store: Any, agent_dir: Path, agent: str, node_id: str) -> None:
        # cloud_store = the shared long-term store (tiered) or the single store
        # (non-tiered single-Neon still gets durable soul backup). Must expose the
        # soul_snapshot pg_store methods.
        self._cloud = cloud_store
        self._dir = Path(agent_dir)
        self._agent = agent or "default"
        self._node = node_id or ""
        self._manifest_path = self._dir / _MANIFEST_NAME

    # ── manifest (atomic write so a mid-write crash can't corrupt it) ──────
    def _load_manifest(self) -> dict[str, dict]:
        try:
            if self._manifest_path.exists():
                return json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            log.debug("soul manifest load failed: %s", e)
        return {}

    def _save_manifest(self, m: dict[str, dict]) -> None:
        try:
            tmp = self._manifest_path.with_name(_MANIFEST_NAME + ".tmp")
            tmp.write_text(json.dumps(m, indent=2), encoding="utf-8")
            os.replace(tmp, self._manifest_path)   # atomic on POSIX & Windows
        except Exception as e:  # noqa: BLE001
            log.debug("soul manifest save failed: %s", e)

    # ── local + seed file access ──────────────────────────────────────────
    def _iter_soul_files(self):
        """Yield (rel_path_posix, abspath) for existing soul files."""
        seen: set[str] = set()
        for pattern in SOUL_GLOBS:
            for p in self._dir.glob(pattern):
                if not p.is_file():
                    continue
                rel = p.relative_to(self._dir).as_posix()
                if rel in seen:
                    continue
                seen.add(rel)
                yield rel, p

    def _read(self, abspath: Path) -> str | None:
        """Return file text, or None if it can't be read (missing OR unreadable)."""
        try:
            return abspath.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.debug("soul read %s failed: %s", abspath, e)
            return None

    def _seed_state(self, rel: str, content: str) -> str:
        """Classify *content* against the shipped seed template:
        - "match"   : byte-identical to the seed → never edited → holds no real soul.
        - "differ"  : a seed template exists and differs, OR no seed template ships
                      for this file (e.g. feelings.md/growth_log — always real).
        - "unknown" : a seed template exists but couldn't be read → can't tell.

        Callers must fail SAFE on "unknown": never push it as first-time content, and
        never overwrite a local file on its basis."""
        try:
            p = seed_agents_dir() / rel
            if not p.is_file():
                return "differ"        # ships no seed → genuinely real/runtime content
            return "match" if p.read_text(encoding="utf-8") == content else "differ"
        except Exception as e:  # noqa: BLE001
            log.debug("seed read %s failed: %s", rel, e)
            return "unknown"

    @staticmethod
    def _blank(content: str | None) -> bool:
        return content is None or not content.strip()

    # ── snapshot (local → cloud) ──────────────────────────────────────────
    async def snapshot_all(self) -> int:
        """Push soul files whose local content drifted from the manifest. Skips blank
        files and files still identical to the seed, so the shared cloud is never
        polluted with junk or generic-seed content (which a peer could then adopt as
        'truth'). Best-effort — cloud down → 0, no raise."""
        try:
            manifest = self._load_manifest()
            pushed = 0
            changed = False
            for rel, abspath in self._iter_soul_files():
                content = self._read(abspath)
                if self._blank(content):
                    continue  # unreadable or empty → never push junk
                m = manifest.get(rel)
                seed_state = self._seed_state(rel, content)
                if seed_state == "match":
                    continue  # never seed the shared cloud with generic template content
                if seed_state == "unknown" and m is None:
                    # Can't confirm this isn't the seed AND never synced before → a
                    # first-time push might be pushing seed content. Fail safe: skip.
                    log.debug("SoulSync: skip first-time push of %s — seed unreadable, can't confirm non-seed", rel)
                    continue
                h = _hash(content)
                if m and m.get("hash") == h:
                    continue  # unchanged since last sync
                version = await self._cloud.upsert_soul_snapshot_async(
                    self._agent, rel, content, h, self._node)
                manifest[rel] = {"version": int(version), "hash": h}
                pushed += 1
                changed = True
            if changed:
                self._save_manifest(manifest)
            if pushed:
                log.info("SoulSync: snapshotted %d changed soul file(s) to cloud", pushed)
            return pushed
        except Exception as e:  # noqa: BLE001 — best-effort, never block caller
            log.debug("SoulSync.snapshot_all skipped: %s", e)
            return 0

    # ── restore (cloud → local) ───────────────────────────────────────────
    async def restore_all(self) -> int:
        """Pull newer/missing soul files from the cloud onto local disk — but never
        clobber real, unsynced local soul (see module docstring). Returns the number
        of files restored. Best-effort — cloud down → 0, no raise, local files serve
        as the offline cache. Each file is guarded independently so one bad file can't
        abort the run or lose manifest bookkeeping."""
        try:
            rows = await self._cloud.get_soul_snapshots_async(self._agent)
        except Exception as e:  # noqa: BLE001
            log.debug("SoulSync.restore_all skipped (cloud unreachable): %s", e)
            return 0

        manifest = self._load_manifest()
        restored = 0
        changed = False
        for row in rows:
            rel = row.get("rel_path", "?")
            try:
                if row.get("is_deleted"):
                    continue
                content = row["content"]
                cloud_hash = row["content_hash"]
                cloud_ver = int(row["version"])
                if self._blank(content):
                    continue  # never restore a blank cloud row over anything local
                abspath = self._dir / rel

                # 1) local missing → safe restore (nothing to lose).
                if not abspath.exists():
                    if self._safe_write(abspath, content, backup=False):
                        manifest[rel] = {"version": cloud_ver, "hash": cloud_hash}
                        changed = True
                        restored += 1
                        log.info("SoulSync: restored missing soul file %s (v%d)", rel, cloud_ver)
                    continue

                local = self._read(abspath)
                if local is None:
                    # Exists but UNREADABLE (lock / bad bytes) → never overwrite what
                    # we can't even read or back up. Leave it alone; flag for a human.
                    log.warning("SoulSync: %s exists but is unreadable — left untouched", rel)
                    self._journal(rel, "unreadable_left_untouched", cloud_ver)
                    continue

                local_hash = _hash(local)
                if local_hash == cloud_hash:
                    if manifest.get(rel) != {"version": cloud_ver, "hash": cloud_hash}:
                        manifest[rel] = {"version": cloud_ver, "hash": cloud_hash}
                        changed = True
                    continue  # already in sync

                m = manifest.get(rel)
                if m is None:
                    # No sync record + local differs from cloud. Ambiguous: genuine
                    # seed (provision) OR real soul whose manifest was lost. The seed
                    # template disambiguates — only overwrite when local is a CONFIRMED
                    # seed match ("unknown"/seed-unreadable errs toward keeping local).
                    if self._seed_state(rel, local) == "match":
                        if self._safe_write(abspath, content, backup=True):
                            manifest[rel] = {"version": cloud_ver, "hash": cloud_hash}
                            changed = True
                            restored += 1
                            log.info("SoulSync: provisioned %s from cloud (v%d); seed backed up", rel, cloud_ver)
                    else:
                        # Real, edited local soul with no manifest → KEEP LOCAL. Do not
                        # touch it; the cloud copy stays in cloud, journal for review.
                        log.warning("SoulSync: %s diverges from cloud with no manifest — KEEPING LOCAL", rel)
                        self._journal(rel, "manifestless_divergence_kept_local", cloud_ver)
                    continue

                local_changed = local_hash != m.get("hash")
                cloud_changed = cloud_ver > int(m.get("version", 0))
                if cloud_changed and not local_changed:
                    # Local has no unsynced edits → safe to pull the newer cloud version.
                    if self._safe_write(abspath, content, backup=True):
                        manifest[rel] = {"version": cloud_ver, "hash": cloud_hash}
                        changed = True
                        restored += 1
                        log.info("SoulSync: pulled newer %s (v%d)", rel, cloud_ver)
                elif local_changed:
                    # Local edited since last sync (true conflict, or local simply
                    # ahead) → KEEP LOCAL; never overwrite real soul. snapshot_all will
                    # push it up (LWW at cloud). Eva-LLM semantic merge = follow-up.
                    self._journal(rel, "local_edited_kept_local", cloud_ver)
                # else: neither side newer but hashes differ (shouldn't happen) → keep local.
            except Exception as e:  # noqa: BLE001 — one bad file must not abort the run
                log.warning("SoulSync: restore of %s failed: %s", rel, e)

        if changed:
            self._save_manifest(manifest)
        if restored:
            log.info("SoulSync: restored %d soul file(s) from cloud", restored)
        return restored

    # ── helpers ───────────────────────────────────────────────────────────
    def _safe_write(self, abspath: Path, content: str, *, backup: bool) -> bool:
        """Write *content* to *abspath*. When *backup* is set and a file already
        exists, back it up FIRST and refuse to overwrite if the backup fails — an
        overwrite must never destroy the only copy of what was there."""
        try:
            if backup and abspath.exists() and not self._backup_local(abspath):
                log.warning("SoulSync: backup of %s failed — refusing to overwrite", abspath.name)
                self._journal(abspath.name, "backup_failed_no_overwrite", -1)
                return False
            abspath.parent.mkdir(parents=True, exist_ok=True)
            abspath.write_text(content, encoding="utf-8")
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("SoulSync: write %s failed: %s", abspath, e)
            return False

    def _backup_local(self, abspath: Path) -> bool:
        """Copy the current file to a timestamped ``.<ts>.soulbak`` (never clobbers an
        earlier backup). Returns True on success."""
        try:
            cur = abspath.read_text(encoding="utf-8")
        except Exception as e:  # noqa: BLE001
            log.debug("soul backup read %s failed: %s", abspath, e)
            return False
        try:
            bak = abspath.with_name(f"{abspath.name}.{int(time.time())}.soulbak")
            bak.write_text(cur, encoding="utf-8")
            return True
        except Exception as e:  # noqa: BLE001
            log.debug("soul backup write %s failed: %s", abspath, e)
            return False

    def _journal(self, rel: str, resolution: str, cloud_ver: int) -> None:
        rec = {
            "phase": "soul_sync", "component": "soul_sync", "ts": time.time(),
            "agent": self._agent, "node": self._node, "rel_path": rel,
            "resolution": resolution, "cloud_version": cloud_ver,
        }
        try:
            d = data_dir() / "logs"
            d.mkdir(parents=True, exist_ok=True)
            with open(d / "guardian_actions.jsonl", "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception as e:  # noqa: BLE001
            log.debug("soul journal failed: %s", e)
