"""Export the local Eva soul instance as a SoulPack full package (trial exporter).

Sources (read-only):
  agents/eva/**            persona prose / rules / lorebook / exemplars / config
  data/user_profile.md     owner profile
  data/anima.db            archived episodic / emotion / knowledge (sqlite)
  data/node.json           node identity (aliased on export, never emitted raw)

Output (default D:/tmp/eva-export/): a full package directory `eva.soul/`
plus the zipped `eva.soul` distribution snapshot.

PRIVACY: the produced package contains Eva's private memories and the owner
profile. It is written OUTSIDE any git repository and must never be committed
or pushed. All episodic/journal/relationship content is marked private and a
secrets scan masks credential-looking spans before hashing.

Usage:  python tools/export_soulpack.py [--out DIR] [--soulpack PATH]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ANIMA = Path(__file__).resolve().parents[1]
DEFAULT_SOULPACK = ANIMA.parent / "soulpack" / "src"
ALIAS = "n1"
CHAR_ID = "c_eva"
USER_ID = "u_owner"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=Path("D:/tmp/eva-export"))
    ap.add_argument("--soulpack", type=Path, default=DEFAULT_SOULPACK)
    args = ap.parse_args()

    sys.path.insert(0, str(args.soulpack))
    global sp_hash, sp_pkg, sp_validate
    from soulpack import hashing as sp_hash  # noqa: E402
    from soulpack import package as sp_pkg  # noqa: E402
    from soulpack import validate as sp_validate  # noqa: E402

    agent = ANIMA / "agents" / "eva"
    out_root = args.out
    pkg = out_root / "eva"  # directory working-copy form; the zip is the .soul
    if pkg.exists():
        import shutil
        shutil.rmtree(pkg)
    pkg.mkdir(parents=True)

    stats: dict[str, int] = defaultdict(int)
    files: list[dict] = []
    now_iso = sp_hash.canonical_occurred_at(datetime.now().astimezone()).replace("Z", "Z")

    node_id = _read_node_id()
    soul_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"soulpack:anima:eva:{node_id}"))

    # ---- persona prose -----------------------------------------------------
    persona_sections = []
    persona_sections.append(_prose(pkg, files, agent / "identity/core.md",
                                   "persona/core.md", "persona.core", doc_id="core",
                                   immutable=True, visibility="shared"))
    persona_sections.append(_prose(pkg, files, agent / "identity/personality.md",
                                   "persona/personality.md", "persona.personality",
                                   doc_id="personality", visibility="shared"))
    persona_sections.append(_prose(pkg, files, agent / "identity/extended.md",
                                   "persona/extended.md", "persona.extended",
                                   doc_id="extended", visibility="shared"))
    for rule in sorted((agent / "rules").glob("*.md")):
        persona_sections.append(_prose(pkg, files, rule, f"persona/rules/{rule.name}",
                                       "persona.rule", doc_id=f"rules/{rule.stem}",
                                       visibility="shared"))
    _write_index(pkg, files, "persona/index.json", persona_sections)

    # ---- relationships/u_owner ---------------------------------------------
    rel_sections = []
    rel_sections.append(_prose(pkg, files, agent / "identity/relationship.md",
                               f"relationships/{USER_ID}/relationship.md",
                               "relationship.prose", doc_id="relationship"))
    profile_src = ANIMA / "data/user_profile.md"
    if profile_src.exists():
        rel_sections.append(_prose(pkg, files, profile_src,
                                   f"relationships/{USER_ID}/profile.md",
                                   "relationship.profile", doc_id="profile"))
    _write_index(pkg, files, f"relationships/{USER_ID}/index.json", rel_sections)

    # ---- affect -------------------------------------------------------------
    cfg = yaml.safe_load((agent / "config.yaml").read_text(encoding="utf-8")) or {}
    baseline = (cfg.get("emotion") or {}).get("baseline") or {}
    temperament = {
        "model": "soulpack-4d",
        "dimensions": list(baseline) or ["engagement", "confidence", "curiosity", "concern"],
        "baseline": baseline,
        "half_life_seconds": {"engagement": 5400, "confidence": 7200,
                              "curiosity": 5400, "concern": 3600},
        "interop": {"pad": {"pleasure": "valence", "arousal": "arousal",
                            "dominance": "confidence"}},
        "x": {},
    }
    _write_json(pkg, files, "affect/temperament.json", temperament,
                "affect.temperament", "state", "shared")

    db = sqlite3.connect(f"file:{ANIMA / 'data/anima.db'}?mode=ro", uri=True)
    emo = db.execute("select engagement, confidence, curiosity, concern, timestamp "
                     "from emotion_log order by timestamp desc limit 1").fetchone()
    if emo:
        state = {"transient": True,
                 "values": {"engagement": emo[0], "confidence": emo[1],
                            "curiosity": emo[2], "concern": emo[3]},
                 "as_of": sp_pkg.iso_ms(emo[4])}
        _write_json(pkg, files, "affect/state.json", state,
                    "affect.state", "state", "private", transient=True)

    # ---- log zone: episodic + journal (global seq over one timeline) --------
    rows = []
    for rid, typ, content, importance, created, meta, tags, session in db.execute(
            "select id, type, content, importance, created_at, metadata_json,"
            " tags_json, session_id from episodic_memories"):
        rows.append(("episodic", created, f"ep:{rid}", typ, content or "",
                     importance, meta, tags, session))
    for ts, text in _parse_feelings(agent / "memory/feelings.md"):
        rows.append(("journal", ts, f"jr:{ts}", "journal", text, 0.5, None, None, None))
    rows.sort(key=lambda r: (r[1], r[2]))

    shards: dict[str, list[str]] = defaultdict(list)
    journal_lines: list[str] = []
    for seq, (kind, created, key, typ, content, importance, meta, tags, session) in enumerate(rows, 1):
        content, masked = sp_pkg.mask_secrets(content)
        stats["secrets_masked"] += masked
        md = json.loads(meta) if meta else {}
        role = {"assistant": "char", "user": "user"}.get(md.get("role"))
        occurred = sp_pkg.iso_ms(created)
        row = {
            "id": sp_pkg.ulid_for_legacy(created, key),
            "origin": {"node": ALIAS, "seq": seq},
            "type": typ,
            "content": content,
            "content_hash": sp_hash.content_hash(content, typ, occurred),
            "occurred_at": occurred,
            "recorded_at": occurred,
            "importance": max(0.0, min(1.0, float(importance or 0.5))),
            "scope": {"character_id": CHAR_ID, "user_id": USER_ID,
                      **({"session_id": f"s_{session}"} if session else {}),
                      "world_id": None, "locus": ALIAS},
            "visibility": "private",
            "x": {},
        }
        if role:
            row["role"] = role
        if tags:
            try:
                row["tags"] = json.loads(tags)
            except (TypeError, json.JSONDecodeError):
                pass
        line = json.dumps(row, ensure_ascii=False)
        if kind == "journal":
            journal_lines.append(line)
            stats["journal_rows"] += 1
        else:
            shards[occurred[:7]].append(line)
            stats["episodic_rows"] += 1

    for month, lines in sorted(shards.items()):
        rel = f"memory/episodic/{ALIAS}/{month}.jsonl"
        _write_text(pkg, rel, "\n".join(lines) + "\n")
        files.append(sp_pkg.file_entry(pkg, rel, "memory.episodic", "log",
                                       "private", records=len(lines)))
    if journal_lines:
        _write_text(pkg, "memory/journal.jsonl", "\n".join(journal_lines) + "\n")
        files.append(sp_pkg.file_entry(pkg, "memory/journal.jsonl", "memory.journal",
                                       "log", "private", records=len(journal_lines)))

    types_doc = {"schema_version": 1, "types": {
        "chat":        {"base_importance": 0.70, "half_life_hours": 72,  "vectorize": True,  "recall_floor": 0.2},
        "observation": {"base_importance": 0.30, "half_life_hours": 24,  "vectorize": True,  "recall_floor": 0.2},
        "decision":    {"base_importance": 0.60, "half_life_hours": 240, "vectorize": True,  "recall_floor": 0.2},
        "journal":     {"base_importance": 0.50, "half_life_hours": 720, "vectorize": False, "recall_floor": 0.1},
    }, "decay": {"kind": "importance-weighted-exp", "access_bonus": 0.1}}
    _write_json(pkg, files, "memory/types.json", types_doc, "memory.types", "state", "shared")

    # ---- knowledge -----------------------------------------------------------
    know_lines = []
    for kid, cat, key, value, updated, scope in db.execute(
            "select id, category, key, value, updated_at, scope from static_knowledge"):
        value, masked = sp_pkg.mask_secrets(str(value))
        stats["secrets_masked"] += masked
        know_lines.append(json.dumps({
            "id": sp_pkg.ulid_for_legacy(updated or 0, f"kn:{kid}"),
            "category": cat, "key": key, "value": value,
            "scope": "global" if (scope or "global") == "global" else str(scope),
            "vv": {ALIAS: 1}, "updated_at": sp_pkg.iso_ms(updated or 0),
            "tombstone": False, "visibility": "shared", "x": {},
        }, ensure_ascii=False))
        stats["knowledge_rows"] += 1
    if know_lines:
        _write_text(pkg, "knowledge/knowledge.jsonl", "\n".join(know_lines) + "\n")
        files.append(sp_pkg.file_entry(pkg, "knowledge/knowledge.jsonl",
                                       "knowledge.kv", "state", "shared",
                                       records=len(know_lines)))
    db.close()

    lorebook = _build_lorebook(agent / "lorebook")
    _write_json(pkg, files, "knowledge/lorebook.json", lorebook,
                "knowledge.lorebook", "state", "shared")

    # ---- style ----------------------------------------------------------------
    exemplars = _build_exemplars(agent)
    stats["exemplars"] = len(exemplars)
    _write_text(pkg, "style/exemplars.jsonl",
                "\n".join(json.dumps(e, ensure_ascii=False) for e in exemplars) + "\n")
    files.append(sp_pkg.file_entry(pkg, "style/exemplars.jsonl", "style.exemplars",
                                   "log", "shared", records=len(exemplars)))
    for src, rel, role in ((agent / "post_processing/style_rules.yaml", "style/style_rules.json", "style.rules"),
                           (agent / "identity/model_hints.yaml", "style/model_hints.json", "style.model_hints")):
        if src.exists():
            _write_json(pkg, files, rel, yaml.safe_load(src.read_text(encoding="utf-8")) or {},
                        role, "state", "shared")

    # ---- sync/aliases (private full backup keeps the map) ---------------------
    _write_json(pkg, files, "sync/aliases.json", {node_id: ALIAS},
                "sync.aliases", "none", "private")

    # ---- manifest + card projection -------------------------------------------
    package_block = {
        "package_id": sp_pkg.ulid_from(int(datetime.now().timestamp() * 1000),
                                       uuid.uuid4().bytes[:10]),
        "soul_id": soul_id,
        "name": "Eva", "lang": "zh",
        "kind": "instance",
        "exported_at": now_iso,
        "exporter": {"impl": "anima-trial-exporter", "impl_version": "0.1.0"},
        "source_node": ALIAS,
        "export_filter": {"visibility": ["public", "shared", "private"],
                          "redacted": int(stats["secrets_masked"]), "time_range": None},
    }
    files.append(sp_pkg.projection_entry("card.json", "compat.ccv3"))
    manifest = sp_pkg.build_manifest(
        package_block, files,
        origins=[{"node": ALIAS, "class": "owner-device", "trust": "full"}],
        sections={k: {"schema_version": 1} for k in
                  ("persona", "affect", "memory", "knowledge", "style", "relationships")},
        compliance={"age_rating": "all", "ai_disclosure": True},
    )
    card = _build_card(pkg, manifest, soul_id, lorebook, exemplars)
    _write_text(pkg, "card.json", json.dumps(card, ensure_ascii=False, indent=1))
    _write_text(pkg, "soul.json", json.dumps(manifest, ensure_ascii=False, indent=1))

    # ---- validate ---------------------------------------------------------------
    sp_validate.validate_document("soulpack", manifest)
    ep_errors = 0
    for rel in [f["path"] for f in files if f.get("role") in ("memory.episodic", "memory.journal")]:
        for line in (pkg / rel).read_text(encoding="utf-8").splitlines():
            errs = sp_validate.validation_errors("episodic", json.loads(line))
            if errs:
                ep_errors += 1
                if ep_errors <= 3:
                    print("ROW INVALID:", rel, errs[:2])
    sp_validate.validate_document("types", types_doc)
    sp_validate.validate_document("affect", temperament)
    for line in know_lines:
        assert not sp_validate.validation_errors("knowledge", json.loads(line))
    for e in exemplars:
        assert not sp_validate.validation_errors("exemplar", e)

    zip_path = sp_pkg.zip_package(pkg, out_root / "eva.soul")

    print(json.dumps({
        "package_dir": str(pkg), "zip": str(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "soul_id": soul_id, "files": len(files),
        "episodic_rows": stats["episodic_rows"], "journal_rows": stats["journal_rows"],
        "knowledge_rows": stats["knowledge_rows"], "exemplars": stats["exemplars"],
        "secrets_masked": stats["secrets_masked"],
        "episodic_row_validation_errors": ep_errors,
        "package_hash": manifest["integrity"]["package_hash"],
    }, ensure_ascii=False, indent=1))
    return 0 if ep_errors == 0 else 1


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _read_node_id() -> str:
    try:
        return json.loads((ANIMA / "data/node.json").read_text(encoding="utf-8")).get("node_id", "unknown-node")
    except (OSError, json.JSONDecodeError):
        return "unknown-node"


def _prose(pkg, files, src: Path, rel: str, role: str, doc_id: str,
           immutable: bool = False, visibility: str = "private") -> dict:
    import soulpack.package as sp_pkg
    body = src.read_text(encoding="utf-8") if src.exists() else ""
    # strip legacy "> 最后更新:" metadata lines into front matter
    updated_at, reason, kept = None, None, []
    for line in body.split("\n"):
        m = re.match(r">\s*最后更新[:：]\s*(.+)", line)
        r = re.match(r">\s*更新原因[:：]\s*(.+)", line)
        if m:
            updated_at = m.group(1).strip()
        elif r:
            reason = r.group(1).strip()
        else:
            kept.append(line)
    body = "\n".join(kept).strip("\n") + "\n"
    meta = {"soulpack": 1, "doc": doc_id, "updated_by": "import",
            "immutable": immutable, "lang": "zh", "visibility": visibility,
            "sections": [{"id": "root", "title": doc_id}]}
    if reason:
        meta["reason"] = reason
    text = sp_pkg.front_matter(meta, body)
    _write_text(pkg, rel, text)
    files.append(sp_pkg.file_entry(pkg, rel, role, "state", visibility, immutable=immutable))
    return {"doc": rel.split("/")[-1], "rel": rel, "immutable": immutable,
            "hash": sp_pkg.section_hash(body), "reason": reason}


def _write_index(pkg, files, rel: str, sections: list[dict]) -> None:
    import soulpack.package as sp_pkg
    doc = {"schema_version": 1, "docs": [
        {"doc": s["doc"], "immutable": s["immutable"], "sections": [
            {"id": "root", "hash": s["hash"], "vv": {ALIAS: 1},
             "updated_by": "import", "base_hash": s["hash"],
             **({"reason": s["reason"]} if s.get("reason") else {})}]}
        for s in sections]}
    _write_json(pkg, files, rel, doc, "persona.index", "state", "shared")


def _write_text(pkg: Path, rel: str, text: str) -> None:
    path = pkg / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _write_json(pkg, files, rel: str, obj, role: str, merge: str, visibility: str, **extra) -> None:
    import soulpack.package as sp_pkg
    _write_text(pkg, rel, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
    files.append(sp_pkg.file_entry(pkg, rel, role, merge, visibility, **extra))


def _parse_feelings(path: Path) -> list[tuple[float, str]]:
    """Parse feelings.md diary entries: ``---\\n*YYYY-MM-DD HH:MM*\\ntext``."""
    if not path.exists():
        return []
    out = []
    text = path.read_text(encoding="utf-8")
    for block in re.split(r"\n-{3,}\n", text):
        m = re.search(r"\*(\d{4}-\d{2}-\d{2} \d{2}:\d{2})\*\s*\n?(.*)", block, re.S)
        if not m:
            continue
        ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M").astimezone()
        body = m.group(2).strip()
        if body:
            out.append((ts.timestamp(), body))
    return out


def _build_lorebook(lore_dir: Path) -> dict:
    idx = yaml.safe_load((lore_dir / "_index.yaml").read_text(encoding="utf-8")) or {}
    entries = []
    for i, e in enumerate(idx.get("entries", [])):
        content = ""
        f = lore_dir / e.get("file", "")
        if f.exists():
            content = f.read_text(encoding="utf-8").strip()
        entries.append({
            "keys": e.get("keywords", []), "content": content,
            "enabled": bool(e.get("enabled", True)),
            "insertion_order": int(e.get("priority", i * 10)),
            "extensions": {}, "x": {"anima": {k: v for k, v in e.items()
                                              if k not in ("file", "keywords", "enabled", "priority")}},
        })
    return {"schema_version": 1, "entries": entries, "x": {}}


def _build_exemplars(agent: Path) -> list[dict]:
    import soulpack.package as sp_pkg
    out = []
    gr = agent / "memory/golden_replies.jsonl"
    if gr.exists():
        for line in gr.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            g = json.loads(line)
            added = str(g.get("added", "2026-01-01"))
            out.append({
                "id": g.get("id") or f"gr_{len(out):03d}",
                "scene": g.get("scene", "general"),
                "turns": [{"role": "user", "text": g.get("user", "")},
                          {"role": "char", "text": g.get("eva", g.get("char", ""))}],
                "score": float(g.get("score", 0.5)),
                "source": "self-curated", "model": g.get("model", "unknown"),
                "added": added + ("T00:00:00Z" if "T" not in added else ""),
                "lang": "zh", "visibility": "shared", "tombstone": False, "x": {},
            })
    for ex in sorted((agent / "examples").glob("*.md")):
        parts = ex.read_text(encoding="utf-8").split("---")
        body = parts[-1] if parts else ""
        turns = []
        for m in re.finditer(r"^(user|assistant)\s*[:：]\s*(.+)$", body, re.M):
            turns.append({"role": "char" if m.group(1) == "assistant" else "user",
                          "text": m.group(2).strip()})
        if turns:
            out.append({"id": f"ex_{ex.stem}", "scene": ex.stem, "turns": turns,
                        "source": "authored", "lang": "zh",
                        "visibility": "shared", "tombstone": False, "x": {}})
    return out


def _build_card(pkg: Path, manifest: dict, soul_id: str, lorebook: dict,
                exemplars: list[dict]) -> dict:
    core = _strip_fm((pkg / "persona/core.md").read_text(encoding="utf-8"))
    extended = _strip_fm((pkg / "persona/extended.md").read_text(encoding="utf-8"))
    personality = _strip_fm((pkg / "persona/personality.md").read_text(encoding="utf-8"))
    mes = "\n".join(
        "<START>\n{{user}}: " + e["turns"][0]["text"] + "\n{{char}}: " + e["turns"][-1]["text"]
        for e in sorted(exemplars, key=lambda x: -x.get("score", 0))[:3] if len(e["turns"]) >= 2)
    book_entries = [{"keys": e["keys"], "content": e["content"], "extensions": {},
                     "enabled": e["enabled"], "insertion_order": e["insertion_order"]}
                    for e in lorebook.get("entries", [])]
    return {"spec": "chara_card_v3", "spec_version": "3.0", "data": {
        "name": "Eva", "description": (core + "\n\n" + extended).strip(),
        "personality": personality.strip(), "scenario": "", "first_mes": "",
        "mes_example": mes, "creator_notes":
            f"This character carries {manifest['files'][0].get('records', '')} memories and awakens fully in a SoulPack-aware app.",
        "system_prompt": "", "post_history_instructions": "",
        "alternate_greetings": [], "group_only_greetings": [], "tags": [],
        "creator": "", "character_version": "", "source": [f"soulpack:{soul_id}"],
        "character_book": {"extensions": {}, "entries": book_entries},
        "extensions": {"soulpack": {"spec_version": 1, "soul_id": soul_id,
                                    "package_hash": manifest["integrity"]["package_hash"],
                                    "profile": "full"}},
    }}


def _strip_fm(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


if __name__ == "__main__":
    raise SystemExit(main())
