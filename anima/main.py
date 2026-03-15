"""ANIMA main entry point — orchestration and graceful shutdown."""

from __future__ import annotations

import asyncio
import signal

from anima.config import load_config, get
from anima.utils.logging import setup_logging, get_logger

log = get_logger("main")


async def run() -> None:
    """Main async entry point — starts all subsystems."""
    config = load_config()
    setup_logging(
        level=get("logging.level", "INFO"),
        log_file=get("logging.file"),
        console=False,  # Interactive mode: logs go to file only, not terminal
    )
    log.info("ANIMA starting...")

    shutdown_event = asyncio.Event()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass

    # Import here to avoid circular imports at module level
    from anima.core.event_queue import EventQueue
    from anima.perception.snapshot_cache import SnapshotCache
    from anima.perception.diff_engine import DiffEngine
    from anima.memory.working import WorkingMemory
    from anima.memory.store import MemoryStore
    from anima.emotion.state import EmotionState
    from anima.tools.registry import ToolRegistry
    from anima.tools.executor import ToolExecutor
    from anima.core.rule_engine import RuleEngine
    from anima.llm.router import LLMRouter
    from anima.llm.prompts import PromptBuilder
    from anima.core.heartbeat import HeartbeatEngine
    from anima.core.cognitive import AgenticLoop
    from anima.ui.terminal import TerminalUI
    from anima.dashboard.hub import DashboardHub
    from anima.dashboard.server import DashboardServer
    from anima.llm.usage import UsageTracker

    from anima.core.agents import AgentManager
    from anima.tools.builtin.agent_tools import set_agent_manager

    # Initialize subsystems
    agent_manager = AgentManager(max_concurrent=5)
    set_agent_manager(agent_manager)

    event_queue = EventQueue()
    snapshot_cache = SnapshotCache(
        history_size=get("perception.snapshot_history_size", 10)
    )
    diff_engine = DiffEngine.from_config(get("diff_rules", {}))
    working_memory = WorkingMemory(capacity=get("memory.working_capacity", 20))
    memory_store = await MemoryStore.create(get("memory.db_path", "data/anima.db"))
    emotion_state = EmotionState(baseline=get("emotion.baseline", {}))

    tool_registry = ToolRegistry()
    tool_registry.register_builtins()
    tool_executor = ToolExecutor(
        registry=tool_registry,
        max_risk=get("tools.safety.max_risk_level", 3),
    )

    rule_engine = RuleEngine()
    llm_router = LLMRouter(
        tier1_model=get("llm.tier1.model", "claude-sonnet-4-6"),
        tier2_model=get("llm.tier2.model", "claude-haiku-4-5-20251001"),
        tier1_max_tokens=get("llm.tier1.max_tokens", 4096),
        tier2_max_tokens=get("llm.tier2.max_tokens", 2048),
        daily_budget=get("llm.budget.daily_limit_usd", 5.0),
    )
    prompt_builder = PromptBuilder()

    # Wire LLM to agent manager so internal agents can run agentic loops
    agent_manager.wire_llm(llm_router, tool_executor, tool_registry, prompt_builder)

    heartbeat = HeartbeatEngine(
        event_queue=event_queue,
        snapshot_cache=snapshot_cache,
        diff_engine=diff_engine,
        emotion_state=emotion_state,
        working_memory=working_memory,
        llm_router=llm_router,
        prompt_builder=prompt_builder,
        config=config,
    )

    cognitive = AgenticLoop(
        event_queue=event_queue,
        snapshot_cache=snapshot_cache,
        memory_store=memory_store,
        emotion_state=emotion_state,
        llm_router=llm_router,
        prompt_builder=prompt_builder,
        tool_executor=tool_executor,
        tool_registry=tool_registry,
        config=config,
    )

    terminal = TerminalUI(event_queue=event_queue)

    # ── Dashboard setup ──
    dashboard_hub = DashboardHub()
    dashboard_hub.heartbeat = heartbeat
    dashboard_hub.cognitive = cognitive
    dashboard_hub.event_queue = event_queue
    dashboard_hub.snapshot_cache = snapshot_cache
    dashboard_hub.working_memory = working_memory
    dashboard_hub.memory_store = memory_store
    dashboard_hub.emotion_state = emotion_state
    dashboard_hub.llm_router = llm_router
    dashboard_hub.tool_registry = tool_registry
    usage_tracker = UsageTracker(memory_store)
    llm_router.set_usage_tracker(usage_tracker)
    dashboard_hub.usage_tracker = usage_tracker
    dashboard_hub.agent_manager = agent_manager
    dashboard_hub.config = config

    dashboard_port = get("dashboard.port", 8420)
    dashboard = DashboardServer(
        hub=dashboard_hub,
        port=dashboard_port,
    )

    # Wire output callback: cognitive → terminal + dashboard
    def on_agent_output(text: str) -> None:
        terminal.display(text)
        dashboard_hub.add_chat_message("agent", text)

    cognitive.set_output_callback(on_agent_output)

    # Wire status callback: cognitive → dashboard activity feed + terminal status
    def on_status(status: dict) -> None:
        stage = status.get("stage", "")
        detail = status.get("detail", "")
        dashboard_hub.add_activity(stage, detail, **{k: v for k, v in status.items() if k not in ("stage", "detail")})
        # Show key stages in terminal
        if stage in ("deciding", "executing"):
            terminal.display_system(f"[{stage}] {detail}")

    cognitive.set_status_callback(on_status)
    agent_manager.set_status_callback(on_status)

    # Wire heartbeat tick callback → dashboard
    def on_tick(tick_record: dict) -> None:
        dashboard_hub.add_activity(
            "heartbeat",
            f"tick #{tick_record['tick']} | CPU {tick_record['cpu']:.0f}% | MEM {tick_record['mem']:.0f}% | sig {tick_record['significance']:.2f}" +
            (f" | files:{tick_record['file_changes']}" if tick_record['file_changes'] else "") +
            (" | ALERT" if tick_record['has_alerts'] else ""),
        )
    heartbeat.set_tick_callback(on_tick)

    # Start all subsystems
    await dashboard.start()

    tasks = [
        asyncio.create_task(heartbeat.start(), name="heartbeat"),
        asyncio.create_task(cognitive.run(), name="cognitive"),
        asyncio.create_task(terminal.start(), name="terminal"),
    ]

    log.info("ANIMA is alive. Dashboard: http://localhost:%d", dashboard_port)

    # Wait for shutdown signal
    await shutdown_event.wait()
    log.info("Shutdown signal received...")

    # Graceful shutdown sequence
    timeout = get("shutdown.timeout_s", 5)

    # 1. Stop heartbeat
    await heartbeat.stop()
    log.info("Heartbeat stopped.")

    # 2. Close event queue
    event_queue.close()

    # 3. Stop terminal + dashboard
    terminal.stop()
    await dashboard.stop()

    # 4. Wait for tasks
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        log.warning("Shutdown timeout — forcing exit.")
        for t in tasks:
            t.cancel()

    # 5. Flush memory
    await memory_store.close()
    log.info("ANIMA shutdown complete.")


def main_entry() -> None:
    """Sync entry point for console_scripts."""
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
