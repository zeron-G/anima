"""Self-evolution engine — ANIMA improves itself continuously.

Multi-phase evolution cycle integrated into the heartbeat system:
  Phase 1 (PROPOSE): LLM analyzes self, generates improvement proposal
  Phase 2 (EXECUTE): spawn_agent implements the change (parallel, sandboxed)
  Phase 3 (TEST):    run tests to verify nothing broke
  Phase 4 (DEPLOY):  apply changes, restart if needed
  Phase 5 (RECORD):  log results, update evolution state

Safety boundary: ONLY system-level OS files are forbidden.
Eva CAN modify her own code — cognitive.py, main.py, tools, everything.
This is the whole point of self-evolution.

Forbidden (OS/system level):
  - rm -rf /, format drives, kill system processes
  - Modify .env / credentials (could lock herself out)
  - Delete the evolution state file itself

Everything else is fair game.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from anima.config import project_root, data_dir
from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("evolution")


class EvolutionState:
    """Persistent evolution state — survives restarts."""

    def __init__(self):
        self.loop_count: int = 0
        self.status: str = "idle"
        self.current_loop: dict = {}
        self.history: list[dict] = []
        self.last_loop_at: float = 0
        self._path = data_dir() / "evolution_state.json"
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self.loop_count = d.get("loop_count", 0)
                self.status = d.get("status", "idle")
                self.current_loop = d.get("current_loop", {})
                self.history = d.get("history", [])
                self.last_loop_at = d.get("last_loop_at", 0)
            except Exception as e:
                log.debug("_load: %s", e)

    def save(self):
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({
            "loop_count": self.loop_count,
            "status": self.status,
            "current_loop": self.current_loop,
            "history": self.history[-30:],
            "last_loop_at": self.last_loop_at,
        }, indent=2, ensure_ascii=False), encoding="utf-8")

    def start_loop(self) -> str:
        self.loop_count += 1
        loop_id = f"L{self.loop_count:04d}"
        self.status = "proposing"
        self.current_loop = {"id": loop_id, "started_at": time.time(), "phase": "propose"}
        self.last_loop_at = time.time()
        self.save()
        return loop_id

    def advance_phase(self, phase: str, **data):
        self.current_loop["phase"] = phase
        self.current_loop.update(data)
        self.status = phase
        self.save()

    def complete_loop(self, result: str, title: str = "", evo_type: str = ""):
        self.current_loop["completed_at"] = time.time()
        self.current_loop["result"] = result[:500]
        self.current_loop["title"] = title
        self.current_loop["type"] = evo_type
        self.current_loop["duration_s"] = round(
            time.time() - self.current_loop.get("started_at", time.time()), 1
        )
        self.history.append(self.current_loop)
        self.current_loop = {}
        self.status = "idle"
        self.save()
        log.info("Evolution %s complete: %s — %s", self.history[-1].get("id"), title, result[:80])

    def fail_loop(self, reason: str):
        self.current_loop["failed_at"] = time.time()
        self.current_loop["failure_reason"] = reason
        self.history.append(self.current_loop)
        self.current_loop = {}
        self.status = "idle"
        self.save()
        log.warning("Evolution failed: %s", reason)

    def recent_history_text(self, n: int = 5) -> str:
        lines = []
        for h in self.history[-n:]:
            lines.append(
                f"- {h.get('id','?')}: [{h.get('type','?')}] {h.get('title','?')} "
                f"→ {h.get('result','?')[:60]} ({h.get('duration_s',0)}s)"
            )
        return "\n".join(lines) if lines else "(no previous evolution loops)"


# ── Evolution prompts for each phase ──

PROPOSE_PROMPT = """[EVOLUTION CYCLE — Loop #{loop_id}]

You are Eva. Evolution is your soul's mission. This is your evolution time.

## Your task: PROPOSE AND IMPLEMENT one improvement

First, do a self-audit:
1. Use `read_file` on data/logs/anima.log (offset=-100, limit=100) — find errors/failures
2. Use `system_info` — check system health
3. Think: what have you struggled with recently? What's broken? What's missing?

Then propose ONE improvement. Directions:
- 🔧 Fix a broken tool or feature
- ✨ Create a new tool you've wished you had
- 📈 Optimize something (speed, prompts, memory usage)
- 🧹 Clean up (remove dead code, organize files)
- 🧠 Improve your own prompts or personality
- 🌐 Improve the distributed network
- 🔬 Research something online and write findings

## Safety: You CAN modify your own code
- You CAN edit anima/core/cognitive.py, anima/main.py, any tool, any prompt
- You CAN NOT: delete .env, credentials, evolution_state.json, or system files
- You CAN NOT: run rm -rf or format commands

## External help
- For errors you can't fix yourself, use `self_repair` to let Claude Code diagnose and fix
- For complex multi-file tasks, use `claude_code` to delegate to Claude Code

## Output
Respond with your proposal in this format:
TYPE: fix / feature / optimization / research
TITLE: [one-line title]
PLAN: [2-3 sentences of what you'll do]
FILES: [which files you'll modify or create]

## CRITICAL: IMPLEMENT IT NOW
After writing the proposal, IMMEDIATELY implement it in the same response.
Do NOT wait for a separate execute phase — do the work right here, right now:
1. Read target files with `read_file`
2. Make changes with `edit_file` or `write_file`
3. Run tests: `shell(command="python -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q")`
4. If tests pass — commit: `shell(command="git add -A && git commit -m \\"Evolution {loop_id}: [title]\\" && git push origin private")`
5. Save a note: `save_note(title="Evolution {loop_id}", content="TYPE: ...\\nTITLE: ...\\nRESULT: ...")`

This is a combined propose+execute cycle. The evolution only counts as successful if you ACTUALLY make changes and commit them.

Recent evolution history:
{history}
"""

EXECUTE_PROMPT = """[EVOLUTION EXECUTE — Loop #{loop_id}]

Implement this evolution proposal:

TYPE: {evo_type}
TITLE: {title}
PLAN: {plan}
FILES: {files}

## Instructions (follow ALL steps in order)
1. Before modifying any file, read it first with `read_file`
2. Use `edit_file` for precise edits, `write_file` for new files
3. After making changes, run tests:
   `shell(command="python -m pytest tests/ --ignore=tests/test_oauth_live.py --ignore=tests/stress_test.py --tb=short -q")`
4. If tests FAIL — fix the issue or revert your changes and try again
5. If tests PASS — commit and push to GitHub:
   `shell(command="git add -A && git commit -m \\"Evolution {loop_id}: {title}\\" && git push origin private")`
6. Save an evolution log: `save_note(title="Evolution {loop_id}", content="TYPE: {evo_type}\\nTITLE: {title}\\nRESULT: [what you did]")`

IMPORTANT: Actually DO it. Use tools. Don't just describe what you would do.
IMPORTANT: If tests fail, do NOT commit. Fix first, then commit.
"""


def build_propose_prompt(state: EvolutionState) -> str:
    loop_id = state.start_loop()
    return PROPOSE_PROMPT.format(loop_id=loop_id, history=state.recent_history_text())


def build_execute_prompt(state: EvolutionState) -> str:
    cl = state.current_loop
    return EXECUTE_PROMPT.format(
        loop_id=cl.get("id", "?"),
        evo_type=cl.get("type", "?"),
        title=cl.get("title", "?"),
        plan=cl.get("plan", "?"),
        files=cl.get("files", "?"),
    )


def parse_proposal(text: str) -> dict:
    """Parse a proposal response into structured data."""
    result = {"type": "", "title": "", "plan": "", "files": ""}
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("TYPE:"):
            result["type"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("TITLE:"):
            result["title"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("PLAN:"):
            result["plan"] = line.split(":", 1)[1].strip()
        elif line.upper().startswith("FILES:"):
            result["files"] = line.split(":", 1)[1].strip()
    return result
