"""Capability stress test — validates ANIMA can match Claude Code / OpenClaw."""
import asyncio, sys, time
sys.stdout.reconfigure(encoding='utf-8')

from anima.config import load_config
from anima.core.event_queue import EventQueue
from anima.perception.snapshot_cache import SnapshotCache
from anima.memory.store import MemoryStore
from anima.emotion.state import EmotionState
from anima.tools.registry import ToolRegistry
from anima.tools.executor import ToolExecutor
from anima.llm.router import LLMRouter
from anima.llm.prompts import PromptBuilder
from anima.core.cognitive import AgenticLoop
from anima.core.agents import AgentManager
from anima.core.scheduler import Scheduler
from anima.tools.builtin.agent_tools import set_agent_manager
from anima.tools.builtin.scheduler_tools import set_scheduler
from anima.llm.usage import UsageTracker
from anima.models.event import Event, EventType, EventPriority

passed = 0
failed = 0

async def main():
    global passed, failed
    config = load_config()
    scheduler = Scheduler()
    set_scheduler(scheduler)
    am = AgentManager(max_concurrent=5)
    set_agent_manager(am)
    eq = EventQueue(); sc = SnapshotCache()
    ms = await MemoryStore.create('data/anima.db')
    em = EmotionState()
    tr = ToolRegistry(); tr.register_builtins()
    te = ToolExecutor(tr, max_risk=3)
    lr = LLMRouter('claude-haiku-4-5-20251001','claude-haiku-4-5-20251001', daily_budget=5.0)
    ut = UsageTracker(ms); lr.set_usage_tracker(ut)
    pb = PromptBuilder()
    am.wire_llm(lr, te, tr)
    loop = AgenticLoop(event_queue=eq, snapshot_cache=sc, memory_store=ms,
        emotion_state=em, llm_router=lr, prompt_builder=pb,
        tool_executor=te, tool_registry=tr, config=config)
    outputs = []; statuses = []
    loop.set_output_callback(lambda t, **kw: outputs.append(t))
    loop.set_status_callback(lambda s: statuses.append(s))
    sc.update({'cpu_percent': 8, 'memory_percent': 35}, [])

    async def ask(text):
        outputs.clear(); statuses.clear()
        await eq.put(Event(type=EventType.USER_MESSAGE, payload={'text': text}, priority=EventPriority.HIGH))
        evt = await eq.get_timeout(2.0)
        t0 = time.time()
        await loop._handle_event(evt)
        elapsed = time.time() - t0
        tools_used = [s.get('detail','').split('(')[0] for s in statuses if s.get('stage')=='executing']
        used_llm = 'thinking' in [s['stage'] for s in statuses]
        return outputs[-1] if outputs else '', tools_used, used_llm, elapsed

    def check(name, result, tools, used_llm, elapsed, expect_tool=None, expect_no_llm=False, expect_in_output=None):
        global passed, failed
        issues = []
        if expect_tool and expect_tool not in tools:
            issues.append(f'missing {expect_tool}')
        if expect_no_llm and used_llm:
            issues.append('LLM called unnecessarily')
        if expect_in_output and expect_in_output not in result:
            issues.append(f'output missing "{expect_in_output}"')
        s = 'PASS' if not issues else 'FAIL'
        if s == 'PASS': passed += 1
        else: failed += 1
        detail = f' tools={tools}' if tools else ''
        print(f'  [{s}] {name} ({elapsed:.1f}s){detail}')
        if issues: print(f'       ISSUES: {issues}')
        if s == 'FAIL' and result: print(f'       output: {result[:100]}')

    print('=' * 50)
    print('  ANIMA Capability Stress Test')
    print('=' * 50)

    # --- Tool selection ---
    print('\n--- Correct Tool Selection ---')
    r,t,l,e = await ask('Find all .yaml files in this project')
    check('Glob search', r,t,l,e, expect_tool='glob_search')

    r,t,l,e = await ask('Search for the word "heartbeat" in Python files under anima/')
    check('Grep search', r,t,l,e, expect_tool='grep_search')

    r,t,l,e = await ask('What is my CPU usage right now?')
    check('System info', r,t,l,e, expect_tool='system_info')

    r,t,l,e = await ask('What time is it?')
    check('Datetime', r,t,l,e, expect_tool='get_datetime')

    r,t,l,e = await ask('Fetch https://httpbin.org/ip and tell me the IP')
    check('Web fetch', r,t,l,e, expect_tool='web_fetch')

    # --- Direct knowledge (no tools) ---
    print('\n--- Direct Knowledge (no tools) ---')
    r,t,l,e = await ask('What is 17 * 23?')
    check('Math', r,t,l,e, expect_in_output='391')

    r,t,l,e = await ask('What is the capital of Japan?')
    check('Geography', r,t,l,e, expect_in_output='Tokyo')

    # --- Rule engine (zero LLM cost) ---
    print('\n--- Rule Engine (zero cost) ---')
    r,t,l,e = await ask('hello')
    check('Greeting', r,t,l,e, expect_no_llm=True)

    r,t,l,e = await ask('hi')
    check('Greeting 2', r,t,l,e, expect_no_llm=True)

    # --- Conversation memory ---
    print('\n--- Conversation Memory ---')
    await ask('Remember this: my favorite color is blue')
    r,t,l,e = await ask('What is my favorite color?')
    check('Remembers context', r,t,l,e, expect_in_output='blue')

    # --- Scheduler ---
    print('\n--- Scheduler ---')
    r,t,l,e = await ask('Schedule a cron job called morning-check to run at 9am daily')
    check('Schedule job', r,t,l,e, expect_tool='schedule_job')

    # --- Summary ---
    usage = ut.get_summary()
    print(f'\n{"=" * 50}')
    print(f'  Results: {passed}/{passed+failed} passed, {failed} failed')
    print(f'  LLM calls this session: {usage["total_calls"]}')
    print(f'  Total tokens: {usage["total_tokens"]}')
    print(f'  Cost: ${usage["total_cost_usd"]:.4f}')
    print(f'{"=" * 50}')

    await ms.close()

asyncio.run(main())
