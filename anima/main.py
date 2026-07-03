"""ANIMA main entry point — orchestration and graceful shutdown."""

from __future__ import annotations

import asyncio
import os
import signal
import sys
import threading
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
_module_state_lock = threading.Lock()


async def _init_core(config: dict) -> dict:
    """Initialize core subsystems: event queue, scheduler, agents, tools, skills."""
    from anima.core.event_queue import EventQueue
    from anima.perception.snapshot_cache import SnapshotCache
    from anima.perception.diff_engine import DiffEngine
    from anima.memory.working import WorkingMemory
    from anima.memory.store import create_memory_store
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
    memory_store = await create_memory_store()
    emotion_state = EmotionState(baseline=get("emotion.baseline", {}))

    tool_registry = ToolRegistry()
    tool_registry.register_builtins()

    # Register evolution tools
    from anima.tools.builtin.evolution_tools import get_evolution_tools, set_evolution_engine as set_evo_tools_engine
    for tool in get_evolution_tools():
        tool_registry.register(tool)

    # Register message_user tool (proactive outreach)
    from anima.tools.builtin.message_user import get_message_user_tool
    tool_registry.register(get_message_user_tool())

    # Register audit tools + issue tracker
    from anima.core.issue_tracker import IssueTracker
    from anima.core.self_audit import SelfAudit
    from anima.tools.builtin.audit_tools import get_audit_tools, set_audit_deps

    issue_tracker = IssueTracker()
    self_audit = SelfAudit(issue_tracker=issue_tracker, event_queue=event_queue)
    set_audit_deps(self_audit, issue_tracker)
    for tool in get_audit_tools():
        tool_registry.register(tool)

    # Register document RAG tools
    from anima.tools.builtin.document_tools import get_document_tools, set_document_store
    from anima.memory.document_store import DocumentStore
    doc_store = DocumentStore(memory_store._db)
    set_document_store(doc_store)
    for tool in get_document_tools():
        tool_registry.register(tool)

    # Replica sync: warm the local failover DB while online, and replay
    # local-only writes back to the primary after an outage. No-op unless both
    # DATABASE_URL and LOCAL_DATABASE_URL are set (and distinct).
    # Skip pg_sync in tiered mode: working + long_term are INDEPENDENT tiers, not a
    # primary/local failover pair, so the failover-replay sync would be wrong there.
    # (Tiered promotion local→cloud is the consolidation job, P3c.)
    if getattr(memory_store, "tiered", False):
        pg_sync = None
        log.info("pg_sync skipped (tiered memory: working/long_term are independent tiers)")
    else:
        from anima.memory.pg_sync import PgSyncManager
        pg_sync = PgSyncManager(memory_store._db,
                                interval_s=get("memory.sync_interval_s", 300))
        await pg_sync.start()

    # Register known remote nodes for cross-node communication
    from anima.tools.builtin.remote import register_node
    from anima.secret_store import resolve as _resolve_secret
    for node_cfg in get("network.remote_nodes", []):
        register_node(node_cfg["name"], node_cfg["host"], node_cfg["user"],
                      _resolve_secret(node_cfg.get("password", "")),
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

    # Register skill management tools
    from anima.tools.builtin.skill_management import get_skill_management_tools, set_skill_loader
    set_skill_loader(skill_loader)
    for tool in get_skill_management_tools():
        tool_registry.register(tool)

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
        "pg_sync": pg_sync,
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

    openai_cfg = get("llm.openai_fallback", {})
    codex_cfg = get("llm.codex_fallback", {})
    deepseek_cfg = get("llm.deepseek_fallback", {})
    openrouter_cfg = get("llm.openrouter_fallback", {})
    llm_router = LLMRouter(
        tier1_model=get("llm.tier1.model", "claude-opus-4-8"),
        tier2_model=get("llm.tier2.model", "claude-opus-4-8"),
        tier1_max_tokens=get("llm.tier1.max_tokens", 8192),
        tier2_max_tokens=get("llm.tier2.max_tokens", 8192),
        daily_budget=get("llm.budget.daily_limit_usd", 5.0),
        local_model=local_cfg.get("model", ""),
        local_max_tokens=local_cfg.get("max_tokens", 4096),
        openai_fallback=openai_cfg.get("model", ""),
        openai_max_tokens=openai_cfg.get("max_tokens", 16384),
        codex_fallback=codex_cfg.get("model", ""),
        codex_max_tokens=codex_cfg.get("max_tokens", 16384),
        deepseek_fallback=deepseek_cfg.get("model", ""),
        deepseek_max_tokens=deepseek_cfg.get("max_tokens", 8192),
        openrouter_fallback=openrouter_cfg.get("model", ""),
        openrouter_max_tokens=openrouter_cfg.get("max_tokens", 8192),
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
            with _module_state_lock:
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
    static_store = StaticKnowledgeStore(memory_store, node_id=node_id_str,
                                        long_term=getattr(memory_store, "long_term", None))

    # Populate Tier 1 static knowledge from environment.md (if exists)
    from anima.config import data_dir
    _env_md_path = data_dir() / "environment.md"
    if _env_md_path.exists():
        try:
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
        long_term=getattr(memory_store, "long_term", None),
    )

    # ── v3: Conversation Summarizer ──
    conversation_summarizer = ConversationSummarizer(
        llm_router=llm_router,
        summary_interval=get("memory.summary_interval", 20),
        keep_recent=get("memory.keep_recent", 10),
    )
    # S1: persist the rolling summary so it survives restarts. The raw buffer is
    # rebuilt from episodic (source of truth) in load_conversation_from_db; only
    # the LLM-generated summary text is loaded from this file.
    from anima.config import data_dir as _data_dir
    conversation_summarizer.set_save_path(_data_dir() / "conversation_summary.json")

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


async def _init_robotics(core: dict) -> dict:
    """Initialize robotics node manager and register robotics tools."""
    from anima.config import get
    from anima.robotics.manager import RoboticsManager
    from anima.tools.builtin.robotics import get_robotics_tools, set_robotics_manager

    robotics_manager = RoboticsManager.from_config(get("robotics", {}))
    await robotics_manager.start()
    set_robotics_manager(robotics_manager)

    for tool in get_robotics_tools():
        core["tool_registry"].register(tool)

    return {"robotics_manager": robotics_manager}


async def _init_heartbeat(config: dict, core: dict, llm: dict) -> dict:
    """Initialize heartbeat engine, idle scheduler, evolution engine."""
    from anima.core.heartbeat import HeartbeatEngine
    from anima.perception.user_activity import UserActivityDetector
    from anima.core.idle_scheduler import IdleDetector, IdleScheduler
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
        memory_decay=llm["memory_decay"],
        llm_router=llm["llm_router"],
        memory_store=core["memory_store"],
        self_audit=core.get("self_audit"),
    )
    heartbeat.set_idle_scheduler(idle_scheduler)

    # Wire env tools and idle tools
    set_env_store(core["memory_store"])
    set_idle_ref(idle_scheduler)

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


async def _init_gossip(config: dict, core: dict, heartbeat_deps: dict) -> dict:
    """Initialize gossip mesh, callbacks, task delegation, memory sync, split-brain detection.

    Returns all gossip-related objects.
    """
    from anima.models.event import Event, EventType, EventPriority
    from anima.network.node import NodeIdentity, NodeState
    from anima.network.gossip import GossipMesh
    from anima.network.discovery import get_local_ip
    from anima.network.split_brain import SplitBrainDetector
    from anima.network.session_router import SessionRouter, TaskDelegate
    from anima.tools.builtin.remote import set_gossip_mesh as set_remote_gossip
    from anima.tools.builtin.remote import set_task_delegate as set_remote_task_delegate

    # Futures for gossip-delegated task results: source_tag -> Future
    gossip_task_futures: dict[str, asyncio.Future] = {}
    # Output buffers for multi-segment gossip task responses: source_tag -> [segments]
    gossip_task_buffers: dict[str, list[str]] = {}
    _task_lock = threading.Lock()

    event_queue = core["event_queue"]
    evolution_engine = heartbeat_deps["evolution_engine"]
    heartbeat = heartbeat_deps["heartbeat"]
    runtime_cfg = get("runtime", {})

    node_identity = NodeIdentity()
    node_state = NodeState(
        node_id=node_identity.node_id,
        hostname=__import__("socket").gethostname(),
        ip=get_local_ip(),
        port=get("network.listen_port", 9420),
        compute_tier=int(runtime_cfg.get("compute_tier", 2)),
        agent_name=get("agent.name", "eva"),
        runtime_profile=str(runtime_cfg.get("profile", "default")),
        runtime_role=str(runtime_cfg.get("role", "desktop_supervisor")),
        platform_class=str(runtime_cfg.get("platform", sys.platform)),
        embodiment=str(runtime_cfg.get("embodiment", "virtual")),
        labels=list(runtime_cfg.get("labels", [])),
        max_concurrent=int(runtime_cfg.get("max_concurrent", 5)),
        capabilities=[t.name for t in core["tool_registry"].list_tools()],
    )
    from anima.secret_store import resolve as _resolve_secret
    # Control-plane trust: this node's Ed25519 identity + the authorizer that
    # verifies + role-authorizes inbound control messages (DISTRIBUTED_DESIGN §6).
    from anima.network.keys import NodeKeys
    from anima.network.authz import MeshAuthorizer
    _node_keys = NodeKeys.load_or_create()
    _authorizer = MeshAuthorizer.from_config()
    gossip_mesh = GossipMesh(
        identity=node_identity,
        local_state=node_state,
        network_secret=_resolve_secret(get("network.secret", "")),
        listen_port=get("network.listen_port", 9420),
        bind_host=get("network.bind_host", "*"),
        node_keys=_node_keys,
        authorizer=_authorizer,
    )
    # Configure peers
    peers = get("network.peers", [])
    if peers:
        gossip_mesh.configure_peers(peers)

    # Gossip event callback -> push to ANIMA event queue
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
    session_router = SessionRouter(node_identity.node_id)
    session_router.set_broadcast(gossip_mesh.broadcast_event)

    # Wire gossip callbacks
    # NOTE: cognitive is not yet created — the evolution_deployed handler references
    # it via the network dict's "cognitive_ref" which is set later in _init_cognitive.
    # We use a mutable container so the closure captures the dict, not the value.
    _cognitive_ref: dict[str, Any] = {"cognitive": None}
    _cognitive_lock = threading.Lock()

    def on_network_event_with_sessions(event_data, source_node=""):
        # FAIL-CLOSED wire dispatcher (DISTRIBUTED_DESIGN §6, CRITICAL finding #1):
        # inbound mesh events are handled ONLY by the explicit branches below.
        # Control events (evolution_*/node_discussion) must carry the authorizer's
        # _mesh_trusted stamp (set only after Ed25519 + role authz). Session ops are
        # data-plane but source-bound. ANY unrecognized type is DROPPED — it must
        # never fall through to a tool-enabled USER_MESSAGE.
        etype = event_data.get("type", "")
        trusted = bool(event_data.get("_mesh_trusted"))

        # Session ownership (data-plane): the acting node_id MUST equal the
        # authenticated sender, else any PSK member could release a peer's locks
        # (finding #2).
        if etype in ("session_lock", "session_release"):
            # Require a non-empty authenticated source that matches the acting
            # node_id. The `not source_node` guard is essential — otherwise an
            # empty source_node short-circuits the binding and any PSK holder can
            # release a victim's lock (re-review still-open finding #2).
            if not source_node or event_data.get("node_id") != source_node:
                log.warning("dropping %s: node_id %r != authenticated source %r",
                            etype, event_data.get("node_id"), source_node)
                return
            if etype == "session_lock":
                session_router.handle_remote_lock(
                    event_data.get("session_id", ""), event_data.get("node_id", ""),
                    event_data.get("channel", ""), event_data.get("timestamp", 0))
            else:
                session_router.handle_remote_release(
                    event_data.get("session_id", ""), event_data.get("node_id", ""))
            return

        # ── Control events: require the authorizer's trust stamp ──
        if etype in ("evolution_propose", "evolution_vote", "evolution_deployed", "node_discussion"):
            if not trusted:
                log.warning("dropping control event '%s' from %s: not authorized "
                            "(no _mesh_trusted)", etype, source_node)
                return
        # Evolution voting protocol
        if etype == "evolution_propose":
            # Remote node proposed an evolution. We DO NOT auto-approve — blindly
            # voting "approve" let any peer (or a mesh injector) get arbitrary
            # self-modifying code passed with zero real review (CODE_REVIEW /
            # DISTRIBUTED_DESIGN P0). Real distributed review + quorum + epoch
            # fencing is Phase 5; until then a remote proposal receives no
            # automatic vote and times out (fail-closed → rejected).
            proposal_data = event_data.get("proposal", {})
            from_node = event_data.get("from_node", "")
            log.warning(
                "Received evolution proposal from %s: %s — NOT auto-voting "
                "(distributed review is Phase 5; proposal will time out unless "
                "a real review votes).", from_node[:8], proposal_data.get("title", "?"))
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
            from anima.config import source_tree
            repo = source_tree()
            if repo is None:
                log.info("Evolution sync skipped: not running from a source checkout")
                return
            if not get("evolution.git_remote_sync", False):
                log.info("Evolution remote sync disabled (evolution.git_remote_sync=false) — skipping auto-pull")
                return
            try:
                _sp.run(["git", "pull", "origin", "private"], cwd=str(repo), capture_output=True, timeout=30)
                log.info("Auto-synced code from remote evolution")
                # Check if .py files changed — trigger reload
                diff = _sp.run(["git", "diff", "HEAD~1", "--name-only"], cwd=str(repo), capture_output=True, text=True)
                if diff.stdout and any(f.endswith(".py") for f in diff.stdout.strip().split("\n")):
                    with _cognitive_lock:
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
        # FAIL-CLOSED: an unrecognized inbound mesh event does NOT reach the
        # cognitive loop. Previously this fell through to on_network_event →
        # tool-enabled USER_MESSAGE (HIGH), a critical ungated-injection vector.
        log.debug("dropping unclassified mesh event '%s' from %s (fail-closed)", etype, source_node)

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
    set_remote_gossip(gossip_mesh, node_identity.node_id)

    # ── TaskDelegate: full task protocol over gossip mesh ──
    task_delegate = TaskDelegate(node_identity.node_id)

    async def _eva_task_handler(task):
        """Process a delegated task by injecting it into the cognitive loop."""
        task_msg = task.payload.get("task", "")
        if not task_msg:
            return {"error": "No task message in payload"}

        source_tag = f"gossip_task:{task.task_id}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        with _task_lock:
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
            with _task_lock:
                segments = list(gossip_task_buffers.get(source_tag, []))
            result_text = "\n\n".join(segments) if segments else fut.result()
            return {"result": result_text}
        except asyncio.TimeoutError:
            with _task_lock:
                segments = list(gossip_task_buffers.get(source_tag, []))
            if segments:
                return {"result": "\n\n".join(segments)}
            return {"error": f"Task timed out after {task.timeout}s"}
        finally:
            with _task_lock:
                gossip_task_futures.pop(source_tag, None)
                gossip_task_buffers.pop(source_tag, None)

    task_delegate.register_handler("eva_task", _eva_task_handler)
    gossip_mesh.attach_task_delegate(task_delegate)
    set_remote_task_delegate(task_delegate)

    await gossip_mesh.start()

    # No peer-to-peer memory sync: all nodes share ONE Postgres DB (Neon primary
    # + local failover), so episodic replication between nodes is redundant. The
    # legacy SQLite-per-node MemorySync has been removed.
    memory_store = core["memory_store"]
    memory_sync = None
    log.info("Memory sync disabled — Postgres backend shares one DB across nodes")

    # Split-brain detector
    split_brain = SplitBrainDetector(node_identity)

    # Periodic sync + split-brain check (every 60s via heartbeat)
    async def _periodic_network_tasks():
        while gossip_mesh._running:
            await asyncio.sleep(60)
            # Split-brain check
            split_brain.check(gossip_mesh.get_alive_count())
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

    log.info("Network gossip started: node=%s, port=%d, peers=%d",
             node_identity.node_id, node_state.port, len(peers))

    return {
        "gossip_mesh": gossip_mesh,
        "node_identity": node_identity,
        "node_state": node_state,
        "session_router": session_router,
        "task_delegate": task_delegate,
        "memory_sync": memory_sync,
        "split_brain": split_brain,
        "gossip_task_futures": gossip_task_futures,
        "gossip_task_buffers": gossip_task_buffers,
        "_task_lock": _task_lock,
        "_cognitive_ref": _cognitive_ref,
        "_cognitive_lock": _cognitive_lock,
    }


async def _init_channels(config: dict, core: dict, gossip_result: dict) -> dict:
    """Initialize WebhookChannel and DiscordChannel.

    Returns channels list + active_channels dict.
    """
    from anima.models.event import Event, EventType, EventPriority

    event_queue = core["event_queue"]
    session_router = gossip_result["session_router"]
    gossip_mesh = gossip_result["gossip_mesh"]

    active_channels: dict[str, Any] = {}
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
                # (No cross-node relay: the old broadcast_event(data) sent a
                # typeless payload that now fail-closes at receivers — and pre-fix
                # it was an ungated injection path. If cross-node channel relay is
                # wanted, add it as an authz-gated control event, not a raw payload.)

        dc.set_message_handler(on_discord_message)
        await dc.start()
        channels.append(dc)
        active_channels["discord"] = dc

    # Telegram channel
    if get("channels.telegram.enabled", False):
        from anima.channels.telegram_channel import TelegramChannel
        tg = TelegramChannel(
            token=get("channels.telegram.token", ""),
            allowed_users=get("channels.telegram.allowed_users", []),
        )

        async def on_telegram_message(data):
            session_id = data.get("source", "telegram:unknown")
            if session_router.try_lock(session_id, channel="telegram", user_id=data.get("user", "")):
                await event_queue.put(Event(
                    type=EventType.USER_MESSAGE,
                    payload=data,
                    priority=EventPriority.HIGH,
                    source="telegram",
                ))
                # (No cross-node relay — see the discord handler note above.)

        tg.set_message_handler(on_telegram_message)
        await tg.start()
        channels.append(tg)
        active_channels["telegram"] = tg

    if channels:
        log.info("Network channels started: %d", len(channels))

    return {
        "channels": channels,
        "active_channels": active_channels,
    }


async def _init_network(config: dict, core: dict, llm: dict, heartbeat_deps: dict) -> dict:
    """Initialize distributed network if enabled: gossip, sessions, channels, sync.

    Thin coordinator that delegates to _init_gossip and _init_channels.
    Returns dict with all network objects (None/empty values if network disabled).
    """
    disabled = {
        "gossip_mesh": None,
        "active_channels": {},
        "gossip_task_futures": {},
        "gossip_task_buffers": {},
        "channels": [],
        "session_router": None,
        "task_delegate": None,
        "memory_sync": None,
        "split_brain": None,
        "_task_lock": None,
        "_cognitive_ref": None,
        "_cognitive_lock": None,
    }
    if not get("network.enabled", False):
        return disabled

    # Fail-closed: the mesh signs/verifies gossip ONLY when a secret is set. An
    # empty secret is fail-OPEN → any host reaching :9420 can inject task_delegate
    # → USER_MESSAGE into the cognitive loop; on PiDog that is physical actuator
    # command injection (DISTRIBUTED_DESIGN P0). Refuse to join an unauthenticated
    # mesh — run SINGLE-NODE instead.
    from anima.secret_store import resolve as _resolve_secret
    if not _resolve_secret(get("network.secret", "")).strip() and not get("network.allow_insecure", False):
        log.critical(
            "network.enabled but network.secret is empty — refusing to join the mesh "
            "(unauthenticated gossip = command injection). Running SINGLE-NODE. Set "
            "ANIMA_NETWORK_SECRET, or network.allow_insecure=true to override.")
        return disabled

    try:
        gossip = await _init_gossip(config, core, heartbeat_deps)
    except ImportError as e:
        # network.enabled is true by default; a slim install lacks the mesh deps.
        log.warning(
            "Network mesh disabled — install `anima[network]` (pyzmq/zeroconf/msgpack) to enable it (%s)", e
        )
        return disabled
    channels = await _init_channels(config, core, gossip)
    return {**gossip, **channels}


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

    # ── Multi-user session isolation ──
    from anima.core.session_manager import SessionManager
    session_manager = SessionManager(
        max_sessions=get("sessions.max_sessions", 50),
        session_ttl_s=get("sessions.ttl_s", 3600),
        memory_store=core["memory_store"],
    )
    cognitive.set_session_manager(session_manager)
    heartbeat_deps["heartbeat"].set_session_manager(session_manager)
    heartbeat_deps["heartbeat"].set_user_activity(heartbeat_deps.get("user_activity"))

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
        event_queue=core["event_queue"],
    )

    # Allow network callbacks to reference cognitive (for evolution_deployed handler).
    # When the mesh is disabled, _cognitive_ref is None (not a dict) — skip cleanly.
    if network.get("_cognitive_ref") is not None:
        _cog_lock = network.get("_cognitive_lock")
        if _cog_lock:
            with _cog_lock:
                network["_cognitive_ref"]["cognitive"] = cognitive
        else:
            network["_cognitive_ref"]["cognitive"] = cognitive

    # ── Restore conversation context ──
    # Always load recent conversation from DB — works for ANY restart type
    # (cold restart, watchdog kill, evolution restart, manual restart)
    cognitive.load_conversation_from_db()
    cognitive.restore_emotion_from_db()  # S2: mood survives any restart

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
        "session_manager": session_manager,
        "reloaded_from_checkpoint": checkpoint is not None,
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
    with _module_state_lock:
        _module_state["dashboard_hub"] = dashboard_hub  # wire for model change notifications
    dashboard_hub.usage_tracker = usage_tracker
    dashboard_hub.agent_manager = core["agent_manager"]
    dashboard_hub.scheduler = core["scheduler"]
    dashboard_hub.skill_loader = core["skill_loader"]
    dashboard_hub.gossip_mesh = network.get("gossip_mesh")
    dashboard_hub.task_delegate = network.get("task_delegate")
    dashboard_hub.evolution_engine = heartbeat_deps["evolution_engine"]
    dashboard_hub.idle_scheduler = heartbeat_deps.get("idle_scheduler")
    dashboard_hub.session_manager = cognitive_deps.get("session_manager")
    dashboard_hub.robotics_manager = core.get("robotics_manager")
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
    _task_lock = network.get("_task_lock")  # may be None if network disabled

    # Wire output callback: cognitive → terminal + dashboard + channels
    def on_agent_output(text: str, source: str = "") -> None:
        # Terminal display — must not block Discord/dashboard
        try:
            terminal.display(text)
        except Exception as e:
            log.warning("Terminal display failed: %s", e)

        dashboard_hub.add_chat_message("agent", text)

        # Route response back to a gossip-delegated task (accumulate all segments)
        if source and source.startswith("gossip_task:") and _task_lock:
            with _task_lock:
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

        if source and source.startswith("telegram:"):
            tg = active_channels.get("telegram")
            if tg:
                import asyncio
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(tg.send(source, text))
                except RuntimeError as e:
                    log.debug("Telegram response routing skipped (no running loop): %s", e)

    cognitive.set_output_callback(on_agent_output)

    # Wire message_user tool to output pipeline
    from anima.tools.builtin.message_user import set_output_callback as set_msg_output, set_dashboard_hub as set_msg_hub
    set_msg_output(on_agent_output)
    set_msg_hub(dashboard_hub)

    # H-03: Wire streaming callback for real-time text output
    def on_stream_chunk(chunk: str, event_type: str = "text") -> None:
        try:
            terminal.display_chunk(chunk, event_type)
        except Exception:
            pass
        try:
            dashboard_hub.push_stream_chunk(chunk, event_type)
        except Exception:
            pass

    cognitive.set_stream_callback(on_stream_chunk)

    # Wire status callback: cognitive → dashboard activity feed + terminal status
    def on_status(status: dict) -> None:
        stage = status.get("stage", "")
        detail = status.get("detail", "")
        try:
            dashboard_hub.add_activity(stage, detail, **{k: v for k, v in status.items() if k not in ("stage", "detail")})
        except Exception:
            pass
        # Show key stages in terminal (protected against encoding errors)
        if stage in ("deciding", "executing"):
            try:
                terminal.display_system(f"[{stage}] {detail}")
            except Exception:
                pass  # Terminal encoding errors must never crash cognitive loop
        # Resolve gossip_task futures when event processing completes
        if stage == "idle" and _task_lock:
            with _task_lock:
                items = [(tag, list(buf)) for tag, buf in gossip_task_buffers.items() if buf]
            for source_tag, segments in items:
                with _task_lock:
                    fut = gossip_task_futures.get(source_tag)
                if fut and not fut.done():
                    fut.set_result("\n\n".join(segments))

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
        json_file=get("logging.json_file", False),
    )
    log.info("ANIMA starting...")

    # Startup dependency validation — fast-fail only on critical issues
    from anima.startup_check import verify_dependencies
    startup_issues = verify_dependencies(config)
    has_critical = False
    for severity, msg in startup_issues:
        if severity == "critical":
            log.error("Startup CRITICAL: %s", msg)
            has_critical = True
        elif severity == "warning":
            log.warning("Startup warning: %s", msg)
        else:
            log.info("Startup info: %s", msg)
    if has_critical:
        log.error("Fix critical errors above and restart. Aborting.")
        return False

    shutdown_event = asyncio.Event()

    # Register signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass

    core = await _init_core(config)
    robotics = await _init_robotics(core)
    core.update(robotics)
    # P3e: restore soul files from the shared cloud BEFORE the prompt compiler warms
    # its caches from them — a freshly provisioned / disk-lost node pulls Eva's real
    # persona back down instead of booting the generic seed. Then snapshot up so the
    # cloud is seeded on first run and any edits unflushed by a prior crash are saved.
    # Cloud unreachable → no-op; local files serve as the offline cache.
    try:
        from anima.memory.soul_sync import SoulSync
        from anima.network.node import NodeIdentity
        from anima.config import agent_dir as _agent_dir, agent_name as _agent_name
        _ms0 = core.get("memory_store")
        _cloud0 = getattr(_ms0, "long_term", _ms0)
        _soul = SoulSync(_cloud0, _agent_dir(), _agent_name(), NodeIdentity().node_id)
        core["soul_sync"] = _soul
        await _soul.restore_all()
        await _soul.snapshot_all()
    except Exception as _e:  # noqa: BLE001 — soul sync must never block startup
        log.warning("SoulSync startup skipped: %s", _e)
    llm = await _init_llm(config, core["tool_registry"], core["tool_executor"], core["memory_store"])
    hb = await _init_heartbeat(config, core, llm)
    net = await _init_network(config, core, llm, hb)
    # Stamp this node's identity onto the memory store so every memory is
    # origin-tagged for the distributed self (DISTRIBUTED_DESIGN §3).
    try:
        _ms = core.get("memory_store")
        _ident = net.get("node_identity")
        if _ident is None:
            # Mesh disabled (network.enabled=false, slim install, or fail-closed on a
            # missing secret) → net has no node_identity. Load the PERSISTENT identity
            # directly so origin_node is a STABLE, node-unique key regardless of mesh
            # state — per-locus emotion restore keys off it; a bare hostname would flip
            # across boots (mesh on/off) and reset mood (P3d review).
            from anima.network.node import NodeIdentity
            _ident = NodeIdentity()
        if _ms is not None:
            import socket as _socket
            _origin = getattr(_ident, "node_id", "") or _socket.gethostname()
            _locus = get("runtime.role", "") or getattr(_ident, "runtime_role", "") or ""
            # Set on BOTH tiers (a composite's __getattr__ is get-only, so setting on
            # the composite wouldn't reach .working). getattr falls back to the plain
            # store in the non-tiered case.
            for _st in {id(getattr(_ms, "working", _ms)): getattr(_ms, "working", _ms),
                        id(getattr(_ms, "long_term", None)): getattr(_ms, "long_term", None)}.values():
                if _st is not None:
                    _st.origin_node = _origin
                    _st.locus = _locus
    except Exception as _e:  # noqa: BLE001 — tagging must never block startup
        log.debug("origin-tag wiring skipped: %s", _e)
    cog = await _init_cognitive(config, core, llm, hb, net)
    hub, dashboard = _init_dashboard(core, llm, hb, cog, net, config)
    _wire_callbacks(cog["cognitive"], cog["terminal"], hub, core["agent_manager"], hb["heartbeat"], net)

    # ── Sentinel: self-healing supervisor (P0 — passive observe-only) ──
    sentinel = None
    if get("guardian.enabled", True):
        from anima.guardian import Sentinel, TaskProbe, LlmProbe, DbProbe, MeshProbe, ResourceProbe
        from anima.guardian.fixer import ComponentPolicy, Registry
        from anima.guardian.fixers import DbRecoverFixer, LlmBackstopFixer
        from anima.guardian.signal import Component
        from anima.llm.providers import get_local_server_manager
        _node_identity = net.get("node_identity")
        _db = getattr(core["memory_store"], "_db", None)
        _gcfg = get("guardian", {})
        # Safe, reversible repair strategies (P2). Heavier rungs slot in later.
        registry = Registry([
            LlmBackstopFixer(hub.llm_router, get_local_server_manager()),
            DbRecoverFixer(_db, core.get("pg_sync"), net.get("split_brain")),
        ])
        _comp_cfg = _gcfg.get("components", {}) or {}
        policies = {
            Component(k): ComponentPolicy.from_config(v)
            for k, v in _comp_cfg.items() if k in Component._value2member_map_
        }
        # P4: process-restart escalation. The hook triggers the SAME graceful
        # in-process restart path evolution-reload uses (checkpoint → re-init),
        # and writes the limb marker so a hung in-process restart is still
        # caught by the external watchdog. Budget is the shared guardian Ledger.
        from anima.guardian.handoff import Ledger, write_restart_marker
        _ledger = Ledger()
        _cognitive_ref = cog["cognitive"]

        def _guardian_restart_hook(reason: str) -> None:
            log.warning("Guardian requested process restart: %s", reason)
            try:
                write_restart_marker(reason)
            except Exception:  # noqa: BLE001
                pass
            try:
                _cognitive_ref.reload_manager.request_reload(reason=f"guardian: {reason}")
            except Exception as e:  # noqa: BLE001
                log.error("guardian restart: request_reload failed: %s", e)
            shutdown_event.set()

        # P5: self-repair (LAST resort before DEFEATED). Default OFF — building it
        # does not enable it. Routes a repair through the SAME gated evolution
        # pipeline (no green channel). DB-isolated live verification before enabling.
        from anima.guardian.self_repair import SelfRepairCoordinator
        _rrb = (_gcfg.get("self_repair_budget", {}) or {})
        _repair_coord = SelfRepairCoordinator(
            engine=hb.get("evolution_engine"),
            ledger=_ledger,
            enabled=bool(_gcfg.get("self_repair_enabled", False)),
            max_repairs=int(_rrb.get("max", 1)),
            window_s=int(_rrb.get("window_s", 86400)),
        )

        def _guardian_repair_hook(component: str, reason: str) -> bool:
            return _repair_coord.attempt(component, reason)

        sentinel = Sentinel(
            probes=[
                TaskProbe(),
                LlmProbe(hub.llm_router),
                DbProbe(_db),
                MeshProbe(net.get("gossip_mesh"), _node_identity, net.get("split_brain")),
                ResourceProbe(),
            ],
            registry=registry,
            policies=policies,
            config=_gcfg,
            node_id=getattr(_node_identity, "node_id", "local"),
            restart_hook=_guardian_restart_hook,
            repair_hook=_guardian_repair_hook,
            ledger=_ledger,
        )
        hub.safety_monitor = sentinel
        core["sentinel"] = sentinel

    # Start all subsystems
    await dashboard.start()

    heartbeat = hb["heartbeat"]
    cognitive = cog["cognitive"]
    terminal = cog["terminal"]
    event_queue = core["event_queue"]
    gossip_mesh = net.get("gossip_mesh")
    mcp_manager = core.get("mcp_manager")

    tasks = []
    for _tname, _tcoro in (
        ("heartbeat", heartbeat.start()),
        ("cognitive", cognitive.run()),
        ("terminal", terminal.start()),
    ):
        _t = asyncio.create_task(_tcoro, name=_tname)
        if sentinel:
            sentinel.watch_task(_t, kind=_tname)   # add_done_callback → surfaces silent death
        tasks.append(_t)
    # The supervisor runs as its own task (must outlive what it watches).
    if sentinel:
        tasks.append(asyncio.create_task(sentinel.run(), name="sentinel"))

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

    # ── Boot self-test + known-good anchor + post-evolution auto-revert ──
    # Give the critical tasks a moment to crash (an evolution that breaks a core
    # module at runtime, not import time, dies here), then judge boot health.
    from anima.core.boot_health import (
        boot_selftest, record_known_good, revert_to_known_good,
    )
    from anima.guardian.handoff import consume_restart_marker, write_restart_marker
    await asyncio.sleep(3)
    boot_ok, boot_detail = boot_selftest(core=core, tasks=tasks, hub=hub)
    reloaded = bool(cog.get("reloaded_from_checkpoint"))
    if boot_ok:
        record_known_good()
        # Healthy boot → consume any restart marker so it can't blind the
        # external watchdog (CODE_REVIEW P0-4: an in-process reload never exits,
        # so the watchdog never consumes it; we do it here instead).
        consume_restart_marker()
    else:
        log.error("Boot self-test FAILED: %s (reloaded_from_checkpoint=%s)", boot_detail, reloaded)
        if reloaded and get("evolution.auto_revert_on_bad_boot", True):
            sha = revert_to_known_good(f"boot self-test failed: {boot_detail}")
            if sha:
                # Mark the exit as a requested restart so the watchdog/systemd
                # relaunches a FRESH process that re-imports the reverted code
                # (an in-process re-run would keep the broken modules in memory).
                write_restart_marker(f"auto-revert to {sha[:12]}: bad evolution boot")
                log.warning("Exiting to relaunch on reverted code.")
                shutdown_event.set()

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

    # Stop the Sentinel observing FIRST, so teardown isn't misread as failures.
    sentinel = core.get("sentinel")
    if sentinel:
        sentinel.begin_shutdown()

    # Graceful shutdown sequence
    timeout = get("shutdown.timeout_s", 5)

    # 0. Stop local LLM server (if running)
    try:
        from anima.llm.providers import get_local_server_manager
        lsm = get_local_server_manager()
        if lsm.is_running:
            lsm.stop()
        # Also kill externally-managed server
        if getattr(lsm, "_externally_managed", False):
            lsm._last_used = 0  # force idle
            lsm.check_idle_shutdown()
    except Exception:
        pass

    # 1. Stop heartbeat
    await heartbeat.stop()
    log.info("Heartbeat stopped.")

    # 2. Close event queue
    event_queue.close()

    # 3. Stop terminal + dashboard + network + channels
    terminal.stop()
    await dashboard.stop()
    if core.get("robotics_manager"):
        await core["robotics_manager"].stop()
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

    # 7. Stop replica sync, then flush memory
    pg_sync = core.get("pg_sync")
    if pg_sync:
        await pg_sync.stop()
    # P3e: flush any soul-file edits made this session up to the shared cloud before
    # we go down, so they survive a disk loss and are visible to other nodes.
    _soul = core.get("soul_sync")
    if _soul is not None:
        try:
            n = await _soul.snapshot_all()
            if n:
                log.info("Shutdown flush: snapshotted %d soul file(s) to cloud", n)
        except Exception as e:  # noqa: BLE001
            log.debug("shutdown soul snapshot skipped: %s", e)
    # Tiered memory: flush any pending SALIENT local memories up to the shared cloud
    # long-term store before we go down (best-effort; never blocks shutdown).
    _ms = core.get("memory_store")
    if getattr(_ms, "tiered", False):
        try:
            from anima.memory.decay import MemoryDecay
            n = await MemoryDecay().flush_promote(_ms, llm_router=None)
            if n:
                log.info("Shutdown flush: promoted %d salient memories to long-term", n)
        except Exception as e:  # noqa: BLE001
            log.debug("shutdown promote-flush skipped: %s", e)
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
    # `anima init ...` subcommand (console-script path; `-m anima` routes via __main__)
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        from anima.bootstrap import handle_init
        handle_init(sys.argv[2:])
        return

    import time
    # Restart brake: cap in-process graceful restarts so a bad evolution that
    # boots-then-asks-to-restart can't spin forever (CODE_REVIEW core P2-2).
    # Hard crashes (uncaught exceptions) propagate out to the external watchdog /
    # systemd, which has its own budget — the two brakes are complementary.
    _RESTART_BRAKE_WINDOW_S = 600
    _RESTART_BRAKE_MAX = 5
    restart_times: list[float] = []
    while True:
        try:
            restart = asyncio.run(run())
        except KeyboardInterrupt:
            break

        if not restart:
            break

        now = time.time()
        restart_times = [t for t in restart_times if now - t < _RESTART_BRAKE_WINDOW_S]
        restart_times.append(now)
        if len(restart_times) > _RESTART_BRAKE_MAX:
            log.error(
                "Restart brake tripped: %d in-process restarts within %ds — stopping "
                "to avoid a restart storm. Manual intervention required.",
                len(restart_times), _RESTART_BRAKE_WINDOW_S,
            )
            break

        log.info("Evolution restart #%d — reloading in 2 seconds...", len(restart_times))
        time.sleep(2)  # Brief pause for connections to close cleanly
