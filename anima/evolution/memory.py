"""Evolution Memory — experience database for learning from past evolutions.

Tracks successes, failures, anti-patterns, and goals.
Persisted to data/evolution_memory.yaml.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import yaml

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("evolution.memory")

MEMORY_FILE = data_dir() / "evolution_memory.yaml"


class EvolutionMemory:
    """Persistent experience database."""

    def __init__(self) -> None:
        self.successes: list[dict] = []
        self.failures: list[dict] = []
        self.anti_patterns: list[str] = []
        self.goals: list[dict] = []
        self._load()

    def _load(self) -> None:
        if not MEMORY_FILE.exists():
            return
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            self.successes = data.get("successes", [])
            self.failures = data.get("failures", [])
            self.anti_patterns = data.get("anti_patterns", [])
            self.goals = data.get("goals", [])
            log.info("Loaded evolution memory: %d successes, %d failures, %d goals",
                     len(self.successes), len(self.failures), len(self.goals))
        except Exception as e:
            log.warning("Failed to load evolution memory: %s", e)

    def save(self) -> None:
        try:
            data = {
                "successes": self.successes[-100:],
                "failures": self.failures[-100:],
                "anti_patterns": self.anti_patterns,
                "goals": self.goals,
            }
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                yaml.dump(data, f, allow_unicode=True, default_flow_style=False)
        except Exception as e:
            log.error("Failed to save evolution memory: %s", e)

    def record_success(self, proposal_id: str, type: str, title: str,
                       files: list[str], lesson: str) -> None:
        self.successes.append({
            "id": proposal_id,
            "type": type,
            "title": title,
            "files": files,
            "lesson": lesson,
            "timestamp": time.time(),
        })
        self.save()
        log.info("Recorded success: %s — %s", proposal_id, title)

    def record_failure(self, proposal_id: str, type: str, title: str,
                       reason: str, lesson: str) -> None:
        self.failures.append({
            "id": proposal_id,
            "type": type,
            "title": title,
            "reason": reason,
            "lesson": lesson,
            "timestamp": time.time(),
        })
        self.save()
        log.info("Recorded failure: %s — %s", proposal_id, reason)

    def add_anti_pattern(self, pattern: str) -> None:
        if pattern not in self.anti_patterns:
            self.anti_patterns.append(pattern)
            self.save()

    def get_lessons_for_files(self, files: list[str]) -> list[str]:
        """Get relevant lessons from past evolutions touching these files."""
        lessons = []
        for s in self.successes + self.failures:
            if any(f in s.get("files", []) for f in files):
                if s.get("lesson"):
                    lessons.append(s["lesson"])
        return lessons[-10:]  # Last 10 relevant lessons

    def get_anti_patterns_text(self) -> str:
        if not self.anti_patterns:
            return ""
        return "ANTI-PATTERNS (avoid these):\n" + "\n".join(f"- {p}" for p in self.anti_patterns)

    # ── Goals ──

    def add_goal(self, title: str, priority: int = 3) -> str:
        goal_id = f"GOAL-{len(self.goals)+1:03d}"
        self.goals.append({
            "id": goal_id,
            "title": title,
            "status": "planned",
            "priority": priority,
            "progress": 0.0,
            "evolutions": [],
            "created": time.time(),
        })
        self.save()
        return goal_id

    def update_goal_progress(self, goal_id: str, progress: float,
                             evolution_id: str = "") -> None:
        for g in self.goals:
            if g["id"] == goal_id:
                g["progress"] = min(1.0, progress)
                if evolution_id and evolution_id not in g.get("evolutions", []):
                    g.setdefault("evolutions", []).append(evolution_id)
                if progress >= 1.0:
                    g["status"] = "completed"
                elif progress > 0:
                    g["status"] = "in_progress"
                self.save()
                return

    def get_next_goal(self) -> dict | None:
        """Get highest priority incomplete goal."""
        active = [g for g in self.goals if g["status"] in ("planned", "in_progress")]
        if not active:
            return None
        active.sort(key=lambda g: (-g.get("priority", 3), g.get("created", 0)))
        return active[0]
