"""Environment search tools — query the env_catalog built by EnvScanner."""

from __future__ import annotations

from anima.models.tool_spec import ToolSpec, RiskLevel

_memory_store = None


def set_memory_store(store) -> None:
    global _memory_store
    _memory_store = store


async def _env_search(query: str = "", category: str = "", extension: str = "",
                      type: str = "", limit: int = 20) -> dict:
    """Search the environment catalog (files and directories scanned by Eva)."""
    if not _memory_store:
        return {"error": "Environment catalog not initialized"}
    results = _memory_store.search_env_catalog(
        query=query, category=category, extension=extension,
        file_type=type, limit=limit,
    )
    return {"count": len(results), "results": results}


async def _env_stats() -> dict:
    """Get environment scan statistics and progress."""
    if not _memory_store:
        return {"error": "Environment catalog not initialized"}
    return _memory_store.get_env_stats()


async def _idle_status() -> dict:
    """Get current idle scheduler status — score, level, running tasks."""
    # This is set at module level from main.py
    if not _idle_scheduler_ref:
        return {"error": "Idle scheduler not initialized"}
    return _idle_scheduler_ref.get_status()


_idle_scheduler_ref = None


def set_idle_scheduler(scheduler) -> None:
    global _idle_scheduler_ref
    _idle_scheduler_ref = scheduler


def get_env_tools() -> list[ToolSpec]:
    return [
        ToolSpec(
            name="env_search",
            description="Search Eva's environment catalog — find files, directories, and projects across the entire computer. Results come from the pre-scanned database, not live filesystem queries.",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Search query (matches path, name, or summary)"},
                    "category": {"type": "string", "description": "Filter by category: code, config, document, media, data, binary, archive, directory, other"},
                    "extension": {"type": "string", "description": "Filter by file extension, e.g. '.py'"},
                    "type": {"type": "string", "description": "Filter by type: 'file' or 'directory'"},
                    "limit": {"type": "integer", "description": "Max results (default 20)"},
                },
                "required": [],
            },
            handler=_env_search,
            risk_level=RiskLevel.SAFE,
        ),
        ToolSpec(
            name="env_stats",
            description="Get environment scan statistics — total files, directories, categories, scan progress for each layer.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_env_stats,
            risk_level=RiskLevel.SAFE,
        ),
        ToolSpec(
            name="idle_status",
            description="Get current idle scheduler status — idle_score, level (busy/light/moderate/deep), running background tasks, API budget usage.",
            parameters={"type": "object", "properties": {}, "required": []},
            handler=_idle_status,
            risk_level=RiskLevel.SAFE,
        ),
    ]
