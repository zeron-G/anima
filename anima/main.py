"""ANIMA main entry point — orchestration and graceful shutdown."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from typing import Any
from pathlib import Path

# Global UTF-8 encoding (defense-in-depth, also set in __main__.py)
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

if sys.platform == "win32":
    for _s in [sys.stdout, sys.stderr]:
        if _s and hasattr(_s, "reconfigure"):
            try:
                _s.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass  # Logger not initialized yet; reconfigure is best-effort
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from anima.config import load_config, get, agent_dir
from anima.utils.logging import setup_logging, get_logger

log = get_logger("main")

# Module-level mutable state for cross-function references (e.g. degradation callback → dashboard hub)
_module_state: dict = {}


async def _init_core(config: dict) -> dict:
    """Initialize core subsystems: event queue, scheduler, agents, tools, skills."""
    from anima.core.event_queue import EventQueue
    from anima.perception.snapshot_cache import SnapshotCache
    from anima.perception.diff_engine import DiffEngine
    from anima.memory.working import WorkingMemory
    from anima.memory.store import MemoryStore
    from anima.emotion.state import EmotionState
    from anima.tools.registry import ToolRegistry
    from anima.tools.executor import ToolExecutor
    from anima.core.rule_engine import RuleEngine
    from anima.core.agents import AgentManager
    from anima.tools.builtin.agent_tools import set_agent_manager
    from anima.core.scheduler import Scheduler
    from anima.tools.builtin.scheduler_tools import set_scheduler as set_scheduler_ref
    from anima.skills.loader import SkillLoader

    # Initialize subsystems
    scheduler = Scheduler()
    set_scheduler_ref(scheduler)

    agent_manager = AgentManager(max_concurrent=5)
    set_agent_manager(agent_manager)

    # Clean stale agent tracker entries from previous runs
    from anima.tools.builtin.agent_tracker import cleanup_stale
    cleanup_stale(max_age_s=1800)  # remove agents older than 30min

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

    # Register evolution tools
    from anima.tools.builtin.evolution_tools import get_evolution_tools, set_evolution_engine as set_evo_tools_engine
    for tool in get_evolution_tools():
        tool_registry.register(tool)

    # Register audit tools + issue tracker
    from anima.core.issue_tracker import IssueTracker
    from anima.core.self_audit import SelfAudit
    from anima.tools.builtin.audit_tools import get_audit_tools, set_audit_deps

    issue_tracker = IssueTracker()
    self_audit = SelfAudit(issue_tracker=issue_tracker, event_queue=event_queue)
    set_audit_deps(self_audit, issue_tracker)
    for tool in get_audit_tools():
        tool_registry.register(tool)

    # Register known remote nodes for cross-node communication
    from anima.tools.builtin.remote import register_node
    for node_cfg in get("network.remote_nodes", []):
        register_node(node_cfg["name"], node_cfg["host"], node_cfg["user"], node_cfg["password"],
                      hosts=node_cfg.get("hosts"))

    tool_executor = ToolExecutor(
        registry=tool_registry,
        max_risk=get("tools.safety.max_risk_level", 3),
    )

    # Discover and register skills from ANIMA's own skills/ directory only.
    # External skill dirs (e.g. OpenClaw) can be added via config, NOT auto-discovered.
    # OpenClaw has its own agent managing its skills — ANIMA should not interfere.
    skill_loader = SkillLoader()
    extra_skill_dirs = get("skills.extra_dirs", [])
    for d in extra_skill_dirs:
        p = Path(d)
        if p.exists():
            skill_loader.add_skill_dir(p)
    skill_loader.discover()
    for tool in skill_loader.generate_tools():
        tool_registry.register(tool)
    for cron_job in skill_loader.get_cron_jobs():
        if cron_job["cron_expr"]:
            scheduler.add_job(
                name=cron_job["name"],
                cron_expr=cron_job["cron_expr"],
                prompt=f"Run skill command: cd {cron_job['cwd']} && {cron_job['command']}",
            )

    # ── MCP Server Integration ──
    mcp_manager = None
    if get("mcp_servers", {}):
        try:
            from anima.mcp.manager import MCPManager
            mcp_manager = MCPManager(tool_registry)
            mcp_tool_count = await mcp_manager.start_from_config()
            if mcp_tool_count > 0:
                log.info("MCP: %d tools registered from external servers", mcp_tool_count)
        except ImportError:
            log.warning("MCP SDK not installed — skipping MCP servers (pip install mcp)")
        except Exception as e:
            log.warning("MCP initialization failed: %s", e)

    rule_engine = RuleEngine()

    return {
        "event_queue": event_queue,
        "snapshot_cache": snapshot_cache,
        "diff_engine": diff_engine,
        "working_memory": working_memory,
        "memory_store": memory_store,
        "emotion_state": emotion_state,
        "tool_registry": tool_registry,
        "tool_executor": tool_executor,
        "scheduler": scheduler,
        "agent_manager": agent_manager,
        "skill_loader": skill_loader,
        "rule_engine": rule_engine,
        "mcp_manager": mcp_manager,
        "set_evo_tools_engine": set_evo_tools_engine,
        "issue_tracker": issue_tracker,
        "self_audit": self_audit,
    }


async def _init_llm(config: dict, tool_registry, tool_executor, memory_store) -> dict:
    """Initialize LLM, prompt system, and v3 memory components."""
    from anima.llm.router import LLMRouter
    from anima.llm.prompt_compiler import PromptCompiler
    from anima.llm.token_budget import TokenBudget
    from anima.memory.importance import ImportanceScorer
    from anima.memory.static_store import StaticKnowledgeStore
    from anima.memory.decay import MemoryDecay
    from anima.memory.retriever import MemoryRetriever
    from anima.llm.lorebook import LorebookEngine
    from anima.memory.summarizer import ConversationSummarizer

    # Wire local LLM config into env vars for providers.LocalServerManager
    local_cfg = get("llm.local", {})
    _local_env_map = {
        "base_url": "LOCAL_LLM_BASE_URL",
        "server_path": "LOCAL_LLM_SERVER_PATH",
        "model_path": "LOCAL_LLM_MODEL_PATH",
        "gpu_layers": "LOCAL_LLM_GPU_LAYERS",
        "ctx_size": "LOCAL_LLM_CTX_SIZE",
        "idle_timeout": "LOCAL_LLM_IDLE_TIMEOUT",
    }
    for cfg_key, env_key in _local_env_map.items():
        val = local_cfg.get(cfg_key, "")
        if val:
            os.environ.setdefault(env_key, str(val))

    llm_router = LLMRouter(
        tier1_model=get("llm.tier1.model", "claude-opus-4-6"),
        tier2_model=get("llm.tier2.model", "claude-opus-4-6"),
        tier1_max_tokens=get("llm.tier1.max_tokens", 8192),
        tier2_max_tokens=get("llm.tier2.max_tokens", 8192),
        daily_budget=get("llm.budget.daily_limit_usd", 5.0),
        local_model=local_cfg.get("model", ""),
        local_max_tokens=local_cfg.get("max_tokens", 4096),
    )

    # ── Model degradation/recovery notifications ──
    def _on_model_change(event: str, from_model: str, to_model: str, reason: str):
        """Notify user when LLM model degrades or recovers."""
        if event == "degraded":
            msg = f"⚠️ 模型降级: {from_model} → {to_model} ({reason})"
            log.warning(msg)
        else:
            msg = f"✅ 模型恢复: {from_model} → {to_model}"
            log.info(msg)
        try:
            hub = _module_state.get("dashboard_hub")
            if hub and hasattr(hub, "add_activity"):
                hub.add_activity("llm", msg)
        except Exception:
            pass

    llm_router.set_degradation_callback(_on_model_change)

    # ── v3: PromptCompiler (6-layer compilation) ──
    token_budget = TokenBudget(
        max_context=get("llm.tier1.max_context", 200_000),
        reserve_response=get("llm.tier1.max_tokens", 8192),
    )
    prompt_compiler = PromptCompiler(
        max_context=get("llm.tier1.max_context", 200_000),
        reserve_response=get("llm.tier1.max_tokens", 8192),
    )

    # ── v3: Importance Scorer ──
    importance_scorer = ImportanceScorer()

    # ── v3: Static Knowledge Store (Tier 1 with node partition) ──
    node_id_str = "local"
    if get("network.enabled", False):
        from anima.network.node import NodeIdentity
        _ni = NodeIdentity()
        node_id_str = _ni.node_id
    static_store = StaticKnowledgeStore(memory_store, node_id=node_id_str)

    # Populate Tier 1 static knowledge from environment.md (if exists)
    from anima.config import data_dir
    _env_md_path = data_dir() / "environment.md"
    if _env_md_path.exists():
        try:
            import re as _re
            _env_text = _env_md_path.read_text(encoding="utf-8")
            _env_data = {}
            _current_section = "general"
            for line in _env_text.split("\n"):
                line = line.strip()
                if line.startswith("## "):
                    _current_section = line[3:].strip().lower().replace(" ", "_")
                    _env_data.setdefault(_current_section, {})
                elif line.startswith("- ") and ":" in line:
                    k, _, v = line[2:].partition(":")
                    _env_data.setdefault(_current_section, {})[k.strip()] = v.strip()
                elif "|" in line and not line.startswith("|--"):
                    parts = [p.strip() for p in line.split("|") if p.strip()]
                    if len(parts) >= 2 and parts[0] != "Path":
                        _env_data.setdefault(_current_section, {})[parts[0]] = parts[1]
            _populated = static_store.populate_from_environment(_env_data)
            if _populated:
                log.info("Tier 1 static knowledge populated: %d entries from environment.md", _populated)
        except Exception as e:
            log.debug("Could not populate static knowledge: %s", e)

    # ── v3: Memory Decay Engine ──
    memory_decay = MemoryDecay()

    # ── v3: Memory Retriever (unified RRF fusion) ──
    lorebook_engine = LorebookEngine(agent_dir() / "lorebook")
    memory_retriever = MemoryRetriever(
        memory_store=memory_store,
        static_store=static_store,
        lorebook=lorebook_engine,
        decay=memory_decay,
    )

    # ── v3: Conversation Summarizer ──
    conversation_summarizer = ConversationSummarizer(
        llm_router=llm_router,
        summary_interval=get("memory.summary_interval", 20),
        keep_recent=get("memory.keep_recent", 10),
    )

    # ── v3: Register memory self-edit tools ──
    try:
        from anima.tools.builtin.memory_tools import get_memory_tools, set_memory_deps
        set_memory_deps(memory_store, prompt_compiler)
        for tool in get_memory_tools():
            tool_registry.register(tool)
    except Exception as e:
        log.warning("Memory tools registration failed: %s", e)

    return {
        "llm_router": llm_router,
        "prompt_compiler": prompt_compiler,
        "token_budget": token_budget,
        "importance_scorer": importance_scorer,
        "static_store": static_store,
        "memory_decay": memory_decay,
        "lorebook_engine": lorebook_engine,
        "memory_retriever": memory_retriever,
        "conversation_summarizer": conversation_summarizer,
    }


async def _init_heartbeat(config: dict, core: dict, llm: dict) -> dict:
    """Initialize heartbeat engine, idle scheduler, evolution engine."""
    from anima.core.heartbeat import HeartbeatEngine
    from anima.perception.user_activity import UserActivityDetector
    from anima.core.idle_scheduler import IdleDetector, IdleScheduler
    from anima.core.idle_scheduler import set_memory_decay as set_idle_memory_decay
    from anima.tools.builtin.env_tools import set_memory_store as set_env_store, set_idle_scheduler as set_idle_ref
    from anima.evolution.engine import EvolutionEngine

    # Wire LLM to agent manager so internal agents can run agentic loops
    core["agent_manager"].wire_llm(
        llm["llm_router"], core["tool_executor"], core["tool_registry"],
    )

    heartbeat = HeartbeatEngine(
        event_queue=core["event_queue"],
        snapshot_cache=core["snapshot_cache"],
        diff_engine=core["diff_engine"],
        emotion_state=core["emotion_state"],
        working_memory=core["working_memory"],
        llm_router=llm["llm_router"],
        config=config,
    )

    heartbeat.set_scheduler(core["scheduler"])

    # ── Idle Scheduler ──
    idle_cfg = get("idle_scheduler", {})
    user_activity = UserActivityDetector(
        use_system_api=idle_cfg.get("user_activity", {}).get("use_system_api", True),
    )
    idle_detector = IdleDetector(
        user_activity=user_activity,
        event_queue=core["event_queue"],
        weights=idle_cfg.get("weights"),
    )
    idle_scheduler = IdleScheduler(
        idle_detector=idle_detector,
        event_queue=core["event_queue"],
        config=idle_cfg,
    )
    heartbeat.set_idle_scheduler(idle_scheduler)

    # Wire env tools and idle tools
    set_env_store(core["memory_store"])
    set_idle_ref(idle_scheduler)

    # Wire memory decay into idle scheduler for deep consolidation tasks
    set_idle_memory_decay(llm["memory_decay"], llm["llm_router"], core["memory_store"])

    # Wire self-audit into idle scheduler for direct tier 1-3 execution
    from anima.core.idle_scheduler import set_self_audit as set_idle_audit
    set_idle_audit(core.get("self_audit"))

    # ── Evolution engine v2 ──
    evolution_engine = EvolutionEngine()
    heartbeat.set_evolution_engine(evolution_engine)
    core["set_evo_tools_engine"](evolution_engine)
    idle_scheduler.set_evolution_engine(evolution_engine)

    return {
        "heartbeat": heartbeat,
        "user_activity": user_activity,
        "idle_detector": idle_detector,
        "idle_scheduler": idle_scheduler,
        "evolution_engine": evolution_engine,
        "idle_cfg": idle_cfg,
    }


async def _init_network(config: dict, core: dict, llm: dict, heartbeat_deps: dict) -> dict:
    """Initialize distributed network if enabled: gossip, sessions, channels, sync.

    Returns empty dict if network disabled.
    """
    from anima.models.event import Event, EventType, EventPriority

    # Active channels for response routing (populated if network enabled)
    active_channels: dict[str, Any] = {}
    # Futures for gossip-delegated task results: source_tag → Future
    gossip_task_futures: dict[str, asyncio.Future] = {}
    # Output buffers for multi-segment gossip task responses: source_tag → [segments]
    gossip_task_buffers: dict[str, list[str]] = {}

    event_queue = core["event_queue"]
    evolution_engine = heartbeat_deps["evolution_engine"]
    heartbeat = heartbeat_deps["heartbeat"]

    gossip_mesh = None
    if not get("network.enabled", False):
        return {
            "gossip_mesh": None,
            "active_channels": active_channels,
            "gossip_task_futures": gossip_task_futures,
            "gossip_task_buffers": gossip_task_buffers,
            "channels": [],
        }

    from anima.network.node import NodeIdentity, NodeState
    from anima.network.gossip import GossipMesh
    from anima.network.discovery import get_local_ip
    from anima.network.sync import MemorySync
    from anima.network.split_brain import SplitBrainDetector

    node_identity = NodeIdentity()
    node_state = NodeState(
        node_id=node_identity.node_id,
        hostname=__import__("socket").gethostname(),
        ip=get_local_ip(),
        port=get("network.listen_port", 9420),
        agent_name=get("agent.name", "eva"),
        capabilities=[t.name for t in core["tool_registry"].list_tools()],
    )
    gossip_mesh = GossipMesh(
        identity=node_identity,
        local_state=node_state,
        network_secret=get("network.secret", ""),
        listen_port=get("network.listen_port", 9420),
    )
    # Configure peers
    peers = get("network.peers", [])
    if peers:
        gossip_mesh.configure_peers(peers)

    # Gossip event callback → push to ANIMA event queue
    def on_network_event(event_data):
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(event_queue.put(Event(
                type=EventType.USER_MESSAGE,
                payload=event_data,
                priority=EventPriority.HIGH,
                source="network",
            )))
        except Exception as e:
            log.warning("Gossip network event forwarding failed: %s", e)

    # Session router
    from anima.network.session_router import SessionRouter
    session_router = SessionRouter(node_identity.node_id)
    session_router.set_broadcast(gossip_mesh.broadcast_event)

    # Wire gossip callbacks
    # NOTE: cognitive is not yet created — the evolution_deployed handler references
    # it via the network dict's "cognitive_ref" which is set later in _init_cognitive.
    # We use a mutable container so the closure captures the dict, not the value.
    _cognitive_ref: dict[str, Any] = {"cognitive": None}

    def on_network_event_with_sessions(event_data):
        # Handle session lock/release events from other nodes
        etype = event_data.get("type", "")
        if etype == "session_lock":
            session_router.handle_remote_lock(
                event_data.get("session_id", ""),
                event_data.get("node_id", ""),
                event_data.get("channel", ""),
                event_data.get("timestamp", 0),
            )
            return
        if etype == "session_release":
            session_router.handle_remote_release(
                event_data.get("session_id", ""),
                event_data.get("node_id", ""),
            )
            return
        # Evolution voting protocol
        if etype == "evolution_propose":
            # Remote node proposed an evolution — auto-vote approve (single reviewer)
            proposal_data = event_data.get("proposal", {})
            from_node = event_data.get("from_node", "")
            log.info("Received evolution proposal from %s: %s", from_node[:8], proposal_data.get("title", "?"))
            # Vote approve (can add smarter review logic later)
            if gossip_mesh:
                gossip_mesh.broadcast_event({
                    "type": "evolution_vote",
                    "proposal_id": proposal_data.get("id", ""),
                    "node_id": node_identity.node_id,
                    "vote": "approve",
                    "reason": "auto-approved by peer",
                })
            return
        if etype == "evolution_vote":
            evolution_engine.consensus.handle_vote(
                event_data.get("proposal_id", ""),
                event_data.get("node_id", ""),
                event_data.get("vote", "abstain"),
                event_data.get("reason", ""),
            )
            return
        if etype == "evolution_deployed":
            # Remote node deployed an evolution — auto-sync
            commit_hash = event_data.get("commit_hash", "")
            title = event_data.get("title", "")
            log.info("Remote evolution deployed: %s (%s)", title, commit_hash[:8] if commit_hash else "?")
            # Auto git pull + hot-reload
            import subprocess as _sp
            from anima.config import project_root
            try:
                _sp.run(["git", "pull", "origin", "private"], cwd=str(project_root()), capture_output=True, timeout=30)
                log.info("Auto-synced code from remote evolution")
                # Check if .py files changed — trigger reload
                diff = _sp.run(["git", "diff", "HEAD~1", "--name-only"], cwd=str(project_root()), capture_output=True, text=True)
                if diff.stdout and any(f.endswith(".py") for f in diff.stdout.strip().split("\n")):
                    _cog = _cognitive_ref.get("cognitive")
                    if _cog:
                        _cog.reload_manager._restart_requested = True
                        _cog.reload_manager._restart_reason = f"Synced evolution: {title}"
            except Exception as e:
                log.warning("Code sync failed: %s", e)
            return
        # Node discussion protocol
        if etype == "node_discussion":
            from_node = event_data.get("from_node", "unknown")
            question = event_data.get("question", "")
            topic = event_data.get("topic", "general")
            log.info("Discussion from %s [%s]: %s", from_node[:8], topic, question[:80])
            # Inject as a user message so Eva processes it
            import asyncio as _aio
            try:
                loop = _aio.get_running_loop()
                loop.create_task(event_queue.put(Event(
                    type=EventType.USER_MESSAGE,
                    payload={
                        "text": f"[Node discussion from {from_node}] Topic: {topic}\n\n{question}",
                        "source": f"discussion:{from_node}",
                    },
                    priority=EventPriority.NORMAL,
                    source=f"discussion:{from_node}",
                )))
            except Exception as e:
                log.warning("Node discussion event injection failed: %s", e)
            return
        if etype == "node_discussion_reply":
            # Reply to our discussion — log it
            log.info("Discussion reply from %s: %s", event_data.get("from_node", "?")[:8], event_data.get("reply", "")[:80])
            return
        # Regular event — forward to ANIMA if we own the session or it's unowned
        on_network_event(event_data)

    def on_node_alive(node_id, state):
        on_network_event({
            "type": "node_status_change",
            "node_id": node_id,
            "status": "alive",
            "hostname": getattr(state, "hostname", None),
        })

    def on_node_dead(node_id, state):
        released = session_router.release_all_for_node(node_id)
        if released:
            log.warning("Released %d sessions from dead node %s", len(released), node_id)
        on_network_event({
            "type": "node_status_change",
            "node_id": node_id,
            "status": "dead",
            "hostname": getattr(state, "hostname", None),
        })

    gossip_mesh.set_callbacks(
        on_event=on_network_event_with_sessions,
        on_node_alive=on_node_alive,
        on_node_dead=on_node_dead,
    )
    heartbeat.set_gossip_mesh(gossip_mesh)
    evolution_engine.wire(gossip_mesh=gossip_mesh)

    # Wire gossip mesh to remote tools for task delegation
    from anima.tools.builtin.remote import set_gossip_mesh as set_remote_gossip
    set_remote_gossip(gossip_mesh, node_identity.node_id)

    # ── TaskDelegate: full task protocol over gossip mesh ──
    from anima.network.session_router import TaskDelegate
    from anima.tools.builtin.remote import set_task_delegate as set_remote_task_delegate

    task_delegate = TaskDelegate(node_identity.node_id)

    async def _eva_task_handler(task):
        """Process a delegated task by injecting it into the cognitive loop."""
        task_msg = task.payload.get("task", "")
        if not task_msg:
            return {"error": "No task message in payload"}

        source_tag = f"gossip_task:{task.task_id}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        gossip_task_futures[source_tag] = fut
        gossip_task_buffers[source_tag] = []

        await event_queue.put(Event(
            type=EventType.USER_MESSAGE,
            payload={"text": task_msg, "source": source_tag},
            priority=EventPriority.NORMAL,
            source=source_tag,
        ))

        try:
            await asyncio.wait_for(asyncio.shield(fut), timeout=task.timeout)
            segments = gossip_task_buffers.get(source_tag, [])
            result_text = "\n\n".join(segments) if segments else fut.result()
            return {"result": result_text}
        except asyncio.TimeoutError:
            segments = gossip_task_buffers.get(source_tag, [])
            if segments:
                return {"result": "\n\n".join(segments)}
            return {"error": f"Task timed out after {task.timeout}s"}
        finally:
            gossip_task_futures.pop(source_tag, None)
            gossip_task_buffers.pop(source_tag, None)

    task_delegate.register_handler("eva_task", _eva_task_handler)
    gossip_mesh.attach_task_delegate(task_delegate)
    set_remote_task_delegate(task_delegate)

    await gossip_mesh.start()

    # Memory sync service
    memory_store = core["memory_store"]
    sync_port = get("network.listen_port", 9420) + 2  # Convention: sync = gossip + 2
    memory_sync = MemorySync(memory_store, node_identity.node_id, listen_port=sync_port)
    memory_sync._ensure_sync_columns()
    await memory_sync.start()

    # Split-brain detector
    split_brain = SplitBrainDetector(node_identity)

    # Periodic sync + split-brain check (every 60s via heartbeat)
    async def _periodic_network_tasks():
        while gossip_mesh._running:
            await asyncio.sleep(60)
            # Split-brain check
            split_brain.check(gossip_mesh.get_alive_count())
            # Sync with random alive peer
            alive = gossip_mesh.get_alive_peers()
            if alive:
                import random
                peer_id = random.choice(list(alive.keys()))
                peer_state = alive[peer_id]
                sync_addr = f"{peer_state.ip}:{peer_state.port + 2}"
                await memory_sync.sync_with_peer(sync_addr, peer_id)
            # Cleanup stale registered nodes
            node_identity.unregister_stale_nodes()
            # Cleanup expired tasks and sessions
            expired_tasks = task_delegate.cleanup_expired()
            if expired_tasks:
                log.info("TaskDelegate: cleaned up %d expired tasks", expired_tasks)
            expired_sessions = session_router.cleanup_expired()
            if expired_sessions:
                log.info("SessionRouter: cleaned up %d expired sessions", len(expired_sessions))

    asyncio.create_task(_periodic_network_tasks(), name="network_tasks")

    # Start channels
    channels = []
    # Webhook channel
    if get("channels.webhook.enabled", False):
        from anima.channels.webhook_channel import WebhookChannel
        wh = WebhookChannel(
            port=get("channels.webhook.port", 9421),
            secret=get("channels.webhook.secret", ""),
        )

        async def on_webhook_message(data):
            session_id = session_router.get_session_id("webhook", data.get("user", ""))
            if session_router.try_lock(session_id, channel="webhook", user_id=data.get("user", "")):
                await event_queue.put(Event(
                    type=EventType.USER_MESSAGE,
                    payload=data,
                    priority=EventPriority.HIGH,
                    source="webhook",
                ))

        wh.set_message_handler(on_webhook_message)
        await wh.start()
        channels.append(wh)

    # Discord channel
    if get("channels.discord.enabled", False):
        from anima.channels.discord_channel import DiscordChannel
        dc = DiscordChannel(
            token=get("channels.discord.token", ""),
            allowed_users=get("channels.discord.allowed_users", []),
        )

        async def on_discord_message(data):
            session_id = data.get("source", "discord:unknown")
            if session_router.try_lock(session_id, channel="discord", user_id=data.get("user", "")):
                await event_queue.put(Event(
                    type=EventType.USER_MESSAGE,
                    payload=data,
                    priority=EventPriority.HIGH,
                    source="discord",
                ))
                # Also broadcast to network so other nodes see it
                await gossip_mesh.broadcast_event(data)

        dc.set_message_handler(on_discord_message)
        await dc.start()
        channels.append(dc)
        active_channels["discord"] = dc

    log.info("Network enabled: node=%s, port=%d, peers=%d, channels=%d",
             node_identity.node_id, node_state.port, len(peers), len(channels))

    return {
        "gossip_mesh": gossip_mesh,
        "session_router": session_router,
        "task_delegate": task_delegate,
        "channels": channels,
        "memory_sync": memory_sync,
        "split_brain": split_brain,
        "active_channels": active_channels,
        "gossip_task_futures": gossip_task_futures,
        "gossip_task_buffers": gossip_task_buffers,
        "_cognitive_ref": _cognitive_ref,
    }


async def _init_cognitive(config: dict, core: dict, llm: dict, heartbeat_deps: dict, network: dict) -> dict:
    """Initialize cognitive loop with all wiring."""
    from anima.core.cognitive import AgenticLoop
    from anima.ui.terminal import TerminalUI
    from anima.core.reload import ReloadManager

    cognitive = AgenticLoop(
        event_queue=core["event_queue"],
        snapshot_cache=core["snapshot_cache"],
        memory_store=core["memory_store"],
        emotion_state=core["emotion_state"],
        llm_router=llm["llm_router"],
        tool_executor=core["tool_executor"],
        tool_registry=core["tool_registry"],
        config=config,
    )
    cognitive.set_heartbeat(heartbeat_deps["heartbeat"])
    cognitive.set_user_activity(heartbeat_deps["user_activity"])
    cognitive.set_idle_scheduler(heartbeat_deps["idle_scheduler"])

    # ── v3: Wire new memory + prompt components into cognitive loop ──
    cognitive.set_prompt_compiler(llm["prompt_compiler"])
    cognitive.set_memory_retriever(llm["memory_retriever"])
    cognitive.set_conversation_summarizer(llm["conversation_summarizer"])
    cognitive.set_importance_scorer(llm["importance_scorer"])
    cognitive.set_token_budget(llm["token_budget"])
    gossip_mesh = network.get("gossip_mesh")
    if gossip_mesh:
        cognitive.set_gossip_mesh(gossip_mesh)

    # Wire evolution engine with reload manager (created later via cognitive)
    heartbeat_deps["evolution_engine"].wire(
        reload_manager=cognitive.reload_manager, agent_manager=core["agent_manager"],
    )

    # Allow network callbacks to reference cognitive (for evolution_deployed handler)
    if "_cognitive_ref" in network:
        network["_cognitive_ref"]["cognitive"] = cognitive

    # ── Restore conversation context ──
    # Always load recent conversation from DB — works for ANY restart type
    # (cold restart, watchdog kill, evolution restart, manual restart)
    cognitive.load_conversation_from_db()

    # Also check for evolution checkpoint (has extra state like emotion)
    checkpoint = ReloadManager.load_checkpoint()
    if checkpoint:
        cognitive.restore_from_checkpoint(checkpoint)
        heartbeat_deps["heartbeat"].mark_as_restart(
            reason=checkpoint.get("reason", "evolution"),
            tick_count=checkpoint.get("tick_count", 0),
        )
        log.info("Evolution restart: restored state from checkpoint (%s)",
                 checkpoint.get("reason", "?"))

    terminal = TerminalUI(event_queue=core["event_queue"])

    return {
        "cognitive": cognitive,
        "terminal": terminal,
    }


def _init_dashboard(core: dict, llm: dict, heartbeat_deps: dict, cognitive_deps: dict, network: dict, config: dict) -> tuple:
    """Initialize dashboard hub and server."""
    from anima.dashboard.hub import DashboardHub
    from anima.dashboard.server import DashboardServer
    from anima.llm.usage import UsageTracker

    dashboard_hub = DashboardHub()
    dashboard_hub.heartbeat = heartbeat_deps["heartbeat"]
    dashboard_hub.cognitive = cognitive_deps["cognitive"]
    dashboard_hub.event_queue = core["event_queue"]
    dashboard_hub.snapshot_cache = core["snapshot_cache"]
    dashboard_hub.working_memory = core["working_memory"]
    dashboard_hub.memory_store = core["memory_store"]
    dashboard_hub.emotion_state = core["emotion_state"]
    dashboard_hub.llm_router = llm["llm_router"]
    dashboard_hub.tool_registry = core["tool_registry"]
    usage_tracker = UsageTracker(core["memory_store"])
    llm["llm_router"].set_usage_tracker(usage_tracker)
    _module_state["dashboard_hub"] = dashboard_hub  # wire for model change notifications
    dashboard_hub.usage_tracker = usage_tracker
    dashboard_hub.agent_manager = core["agent_manager"]
    dashboard_hub.scheduler = core["scheduler"]
    dashboard_hub.skill_loader = core["skill_loader"]
    dashboard_hub.gossip_mesh = network.get("gossip_mesh")
    dashboard_hub.evolution_engine = heartbeat_deps["evolution_engine"]
    dashboard_hub.idle_scheduler = heartbeat_deps.get("idle_scheduler")
    dashboard_hub.config = config

    dashboard_port = get("dashboard.port", 8420)
    dashboard = DashboardServer(
        hub=dashboard_hub,
        port=dashboard_port,
    )

    return dashboard_hub, dashboard


def _wire_callbacks(cognitive, terminal, dashboard_hub, agent_manager, heartbeat, network: dict) -> None:
    """Wire output/status/tick callbacks between subsystems."""
    active_channels = network.get("active_channels", {})
    gossip_task_futures = network.get("gossip_task_futures", {})
    gossip_task_buffers = network.get("gossip_task_buffers", {})

    # Wire output callback: cognitive → terminal + dashboard + channels
    def on_agent_output(text: str, source: str = "") -> None:
        # Terminal display — must not block Discord/dashboard
        try:
            terminal.display(text)
        except Exception as e:
            log.warning("Terminal display failed: %s", e)

        dashboard_hub.add_chat_message("agent", text)

        # Route response back to a gossip-delegated task (accumulate all segments)
        if source and source.startswith("gossip_task:"):
            if source in gossip_task_buffers:
                gossip_task_buffers[source].append(text)

        # Route response back to the originating channel
        if source and source.startswith("discord:"):
            dc = active_channels.get("discord")
            if dc:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(dc.send(source, text))
                except RuntimeError as e:
                    log.debug("Discord response routing skipped (no running loop): %s", e)

    cognitive.set_output_callback(on_agent_output)

    # Wire status callback: cognitive → dashboard activity feed + terminal status
    def on_status(status: dict) -> None:
        stage = status.get("stage", "")
        detail = status.get("detail", "")
        dashboard_hub.add_activity(stage, detail, **{k: v for k, v in status.items() if k not in ("stage", "detail")})
        # Show key stages in terminal
        if stage in ("deciding", "executing"):
            terminal.display_system(f"[{stage}] {detail}")
        # Resolve gossip_task futures when event processing completes
        if stage == "idle":
            for source_tag, buf in list(gossip_task_buffers.items()):
                if buf:
                    fut = gossip_task_futures.get(source_tag)
                    if fut and not fut.done():
                        fut.set_result("\n\n".join(buf))

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


async def run() -> bool:
    """Main async entry point — starts all subsystems.

    Returns True if a restart was requested (evolution hot-reload),
    False for normal shutdown.
    """
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

    core = await _init_core(config)
    llm = await _init_llm(config, core["tool_registry"], core["tool_executor"], core["memory_store"])
    hb = await _init_heartbeat(config, core, llm)
    net = await _init_network(config, core, llm, hb)
    cog = await _init_cognitive(config, core, llm, hb, net)
    hub, dashboard = _init_dashboard(core, llm, hb, cog, net, config)
    _wire_callbacks(cog["cognitive"], cog["terminal"], hub, core["agent_manager"], hb["heartbeat"], net)

    # Start all subsystems
    await dashboard.start()

    heartbeat = hb["heartbeat"]
    cognitive = cog["cognitive"]
    terminal = cog["terminal"]
    event_queue = core["event_queue"]
    gossip_mesh = net.get("gossip_mesh")
    mcp_manager = core.get("mcp_manager")

    tasks = [
        asyncio.create_task(heartbeat.start(), name="heartbeat"),
        asyncio.create_task(cognitive.run(), name="cognitive"),
        asyncio.create_task(terminal.start(), name="terminal"),
    ]

    # ── Environment scanner: Layer 1 on startup ──
    idle_cfg = hb["idle_cfg"]
    env_scan_cfg = idle_cfg.get("env_scan", {})
    if env_scan_cfg.get("enabled", True) and env_scan_cfg.get("layer1_on_startup", True):
        from anima.perception.env_scanner import EnvScanner
        env_scanner = EnvScanner(
            db=core["memory_store"],
            extra_skip_dirs=env_scan_cfg.get("extra_skip_dirs", []),
            extra_high_value_dirs=env_scan_cfg.get("extra_high_value_dirs", []),
            batch_size=env_scan_cfg.get("batch_size", 500),
        )

        async def _env_scan_startup():
            try:
                await env_scanner.scan_layer1()
                log.info("Environment Layer 1 scan completed on startup")
            except Exception as e:
                log.warning("Env Layer 1 scan failed: %s", e)

        asyncio.create_task(_env_scan_startup(), name="env_scan_layer1")

    from anima.network.discovery import get_local_ip as _lip
    dashboard_port = get("dashboard.port", 8420)
    log.info("ANIMA is alive. Dashboard: http://%s:%d", _lip(), dashboard_port)

    # Wait for shutdown signal (from user Ctrl+C or evolution reload)
    # Also monitor cognitive loop's reload manager
    async def _watch_reload():
        while not shutdown_event.is_set():
            if cognitive.reload_manager.restart_requested:
                log.info("Evolution reload detected — initiating restart...")
                shutdown_event.set()
                return
            await asyncio.sleep(2)
    reload_watcher = asyncio.create_task(_watch_reload(), name="reload_watcher")

    await shutdown_event.wait()
    restart_requested = cognitive.reload_manager.restart_requested
    if restart_requested:
        log.info("Shutdown for evolution restart: %s", cognitive.reload_manager.restart_reason)
    else:
        log.info("Shutdown signal received...")

    # Graceful shutdown sequence
    timeout = get("shutdown.timeout_s", 5)

    # 1. Stop heartbeat
    await heartbeat.stop()
    log.info("Heartbeat stopped.")

    # 2. Close event queue
    event_queue.close()

    # 3. Stop terminal + dashboard + network + channels
    terminal.stop()
    await dashboard.stop()
    if gossip_mesh:
        channels = net.get("channels", [])
        for ch in channels:
            await ch.stop()
        memory_sync = net.get("memory_sync")
        if memory_sync:
            await memory_sync.stop()
        await gossip_mesh.stop()

    # 4. Cancel all remaining async tasks
    for t in tasks:
        t.cancel()
    # Also cancel network_tasks if it exists
    for task in asyncio.all_tasks():
        if task.get_name() in ("network_tasks", "reload_watcher"):
            task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*tasks, return_exceptions=True),
            timeout=timeout,
        )
    except (asyncio.TimeoutError, asyncio.CancelledError):
        log.warning("Shutdown timeout — forcing exit.")

    # 5. Clean up reload watcher
    reload_watcher.cancel()
    try:
        await reload_watcher
    except asyncio.CancelledError:
        pass

    # 6. Stop MCP servers
    if mcp_manager:
        await mcp_manager.stop_all()

    # 7. Flush memory
    await core["memory_store"].close()

    if restart_requested:
        log.info("ANIMA shutdown complete — restarting for evolution reload...")
    else:
        log.info("ANIMA shutdown complete.")
    return restart_requested


def main_entry() -> None:
    """Sync entry point for console_scripts.

    Wraps run() in a restart loop for evolution hot-reload.
    When evolution modifies .py files and requests reload:
      1. run() saves checkpoint → graceful shutdown → returns True
      2. This loop re-calls run()
      3. New run() loads checkpoint → restores state → continues
    """
    restart_count = 0
    while True:
        try:
            restart = asyncio.run(run())
        except KeyboardInterrupt:
            break

        if not restart:
            break

        # Evolution restart — brief pause then restart
        restart_count += 1
        import time
        log.info("Evolution restart #%d — reloading in 2 seconds...", restart_count)
        time.sleep(2)  # Brief pause for connections to close cleanly
