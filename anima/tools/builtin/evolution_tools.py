"""Evolution tools — bridge between cognitive loop and evolution engine.

These tools allow Eva to interact with the six-layer evolution pipeline
from within her normal agentic loop.
"""

from __future__ import annotations


from anima.models.tool_spec import ToolSpec, RiskLevel
from anima.utils.logging import get_logger

log = get_logger("tools.evolution")

# Set by main.py during wiring
_evolution_engine = None


def set_evolution_engine(engine) -> None:
    global _evolution_engine
    _evolution_engine = engine


async def _evolution_propose(
    type: str = "feature",
    title: str = "",
    problem: str = "",
    solution: str = "",
    files: str = "",
    risk: str = "low",
    priority: int = 3,
    complexity: str = "small",
    human_confirmed: bool = False,
) -> dict:
    """Submit an evolution proposal to the six-layer pipeline.

    This creates a structured proposal, runs it through consensus,
    then queues it for implementation → test → review → deploy.
    """
    if not _evolution_engine:
        return {"error": "Evolution engine not initialized"}

    if not title:
        return {"error": "title is required"}

    from anima.evolution.proposal import create_proposal

    file_list = [f.strip() for f in files.split(",") if f.strip()] if files else []

    proposal = create_proposal(
        type=type,
        title=title,
        problem=problem,
        solution=solution,
        files=file_list,
        risk=risk,
        priority=priority,
        complexity=complexity,
        human_confirmed=human_confirmed,
    )

    log.info("Eva submitted proposal: %s — %s", proposal.id, title)

    # Submit through the pipeline (consensus → queue)
    result = await _evolution_engine.submit_proposal(proposal)

    return {
        "proposal_id": proposal.id,
        "status": result,
        "title": title,
        "message": f"Proposal {proposal.id} submitted → status: {result}",
    }


async def _evolution_status() -> dict:
    """Get the current evolution pipeline status."""
    if not _evolution_engine:
        return {"error": "Evolution engine not initialized"}

    return _evolution_engine.get_status()


async def _evolution_add_goal(title: str = "", priority: int = 3) -> dict:
    """Add a new evolution goal to the goal system."""
    if not _evolution_engine:
        return {"error": "Evolution engine not initialized"}

    if not title:
        return {"error": "title is required"}

    goal_id = _evolution_engine.memory.add_goal(title, priority)
    return {"goal_id": goal_id, "title": title, "message": f"Goal {goal_id} added"}


async def _evolution_list_goals() -> dict:
    """List all evolution goals and their progress."""
    if not _evolution_engine:
        return {"error": "Evolution engine not initialized"}

    return {
        "goals": _evolution_engine.memory.goals,
        "next_goal": _evolution_engine.memory.get_next_goal(),
    }


async def _evolution_record_lesson(lesson: str = "", pattern_type: str = "anti_pattern") -> dict:
    """Record a lesson learned or anti-pattern from development experience."""
    if not _evolution_engine:
        return {"error": "Evolution engine not initialized"}

    if not lesson:
        return {"error": "lesson is required"}

    if pattern_type == "anti_pattern":
        _evolution_engine.memory.add_anti_pattern(lesson)
        return {"recorded": True, "type": "anti_pattern", "lesson": lesson}

    return {"error": f"Unknown pattern_type: {pattern_type}"}


def get_evolution_tools() -> list[ToolSpec]:
    """Return all evolution-related tools."""
    return [
        ToolSpec(
            name="evolution_propose",
            description=(
                "Submit an evolution proposal to the six-layer pipeline "
                "(Proposal → Consensus → Implement → Test → Review → Deploy). "
                "Use this INSTEAD of directly modifying code when doing evolution tasks. "
                "The pipeline handles isolation (git worktree), testing, review, and safe deployment."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["bugfix", "feature", "refactor", "optimization", "architecture"], "description": "Type of evolution"},
                    "title": {"type": "string", "description": "Short title of the change"},
                    "problem": {"type": "string", "description": "What problem does this solve"},
                    "solution": {"type": "string", "description": "How to solve it"},
                    "files": {"type": "string", "description": "Comma-separated list of files to modify"},
                    "risk": {"type": "string", "enum": ["low", "medium", "high"], "description": "Risk level"},
                    "priority": {"type": "integer", "description": "Priority 1-5 (5=urgent)", "default": 3},
                    "complexity": {"type": "string", "enum": ["trivial", "small", "medium", "large"], "description": "Complexity"},
                    "human_confirmed": {"type": "boolean", "description": "Set true if human has explicitly confirmed this core module change", "default": False},
                },
                "required": ["title", "problem", "solution"],
            },
            risk_level=RiskLevel.MEDIUM,
            handler=_evolution_propose,
        ),
        ToolSpec(
            name="evolution_status",
            description="Get the current evolution pipeline status: running proposal, queue size, cooldown, agent pool, memory stats.",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=_evolution_status,
        ),
        ToolSpec(
            name="evolution_add_goal",
            description="Add a new evolution goal. Goals drive the direction of autonomous evolution.",
            parameters={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Goal title"},
                    "priority": {"type": "integer", "description": "Priority 1-5", "default": 3},
                },
                "required": ["title"],
            },
            risk_level=RiskLevel.LOW,
            handler=_evolution_add_goal,
        ),
        ToolSpec(
            name="evolution_list_goals",
            description="List all evolution goals and their progress.",
            parameters={"type": "object", "properties": {}},
            risk_level=RiskLevel.SAFE,
            handler=_evolution_list_goals,
        ),
        ToolSpec(
            name="evolution_record_lesson",
            description="Record a lesson learned or anti-pattern from development. These are used to avoid repeating mistakes in future evolutions.",
            parameters={
                "type": "object",
                "properties": {
                    "lesson": {"type": "string", "description": "The lesson or anti-pattern to record"},
                    "pattern_type": {"type": "string", "enum": ["anti_pattern"], "default": "anti_pattern"},
                },
                "required": ["lesson"],
            },
            risk_level=RiskLevel.SAFE,
            handler=_evolution_record_lesson,
        ),
    ]
