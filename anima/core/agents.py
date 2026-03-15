"""Multi-agent session manager with true LLM sub-agents.

Architecture that matches Claude Code:

  Eva (main AgenticLoop)
    |-- spawn_agent(type="internal", prompt="research X")
    |     └── Sub-AgenticLoop: own LLM calls + same tools, focused task
    |         └── can spawn its own children
    |-- spawn_agent(type="claude_code", prompt="complex refactor")
    |     └── Claude Code CLI process (full Claude instance)
    |-- spawn_agent(type="shell", prompt="python script.py")
          └── Simple subprocess

Key difference from the old design:
- "internal" agents run a REAL agentic loop (LLM + tools + multi-turn)
- They share ANIMA's tool registry (read_file, shell, web_fetch, etc.)
- They have focused context (parent's task description)
- They can be spawned in parallel
- Results flow back to the parent
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from anima.utils.ids import gen_id
from anima.utils.logging import get_logger

log = get_logger("agents")


@dataclass
class AgentSession:
    id: str = field(default_factory=lambda: gen_id("agent"))
    type: str = ""
    status: str = "pending"
    prompt: str = ""
    result: str = ""
    error: str = ""
    parent_id: str = ""
    created_at: float = field(default_factory=time.time)
    completed_at: float = 0
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "type": self.type, "status": self.status,
            "prompt": self.prompt[:300], "result": self.result[:1000],
            "error": self.error[:300], "parent_id": self.parent_id,
            "created_at": self.created_at, "completed_at": self.completed_at,
            "duration_s": round(self.completed_at - self.created_at, 1) if self.completed_at else 0,
            "metadata": self.metadata,
        }


class AgentManager:
    """Manages sub-agent lifecycle with hierarchy tracking.

    The critical feature: "internal" agents run their own LLM agentic loop
    with the same tools as the parent — exactly like Claude Code's Agent tool.
    """

    def __init__(self, max_concurrent: int = 5) -> None:
        self._sessions: dict[str, AgentSession] = {}
        self._max_concurrent = max_concurrent
        self._tasks: dict[str, asyncio.Task] = {}
        self._status_callback: Callable | None = None
        # These get set by main.py after construction
        self._llm_router = None
        self._tool_executor = None
        self._tool_registry = None
        self._prompt_builder = None

    def wire_llm(self, llm_router, tool_executor, tool_registry, prompt_builder) -> None:
        """Wire LLM components so internal agents can run agentic loops."""
        self._llm_router = llm_router
        self._tool_executor = tool_executor
        self._tool_registry = tool_registry
        self._prompt_builder = prompt_builder

    def set_status_callback(self, cb: Callable) -> None:
        self._status_callback = cb

    def _emit(self, stage: str, detail: str, **kw: Any) -> None:
        if self._status_callback:
            self._status_callback({"stage": stage, "detail": detail, **kw})

    # ── Spawn methods ──

    async def spawn_internal(
        self, prompt: str, parent_id: str = "", timeout: int = 120,
    ) -> AgentSession:
        """Spawn an internal sub-agent with its own LLM agentic loop.

        This is the equivalent of Claude Code's Agent tool:
        - Gets its own LLM conversation
        - Has access to all ANIMA tools (read_file, shell, web_fetch, etc.)
        - Runs multi-turn tool-use until the task is done
        - Returns the final text result
        """
        session = AgentSession(type="internal", prompt=prompt, parent_id=parent_id)
        self._sessions[session.id] = session
        self._emit("agent_spawn", f"internal: {prompt[:80]}", agent_id=session.id)
        task = asyncio.create_task(self._run_internal_agent(session, timeout))
        self._tasks[session.id] = task
        return session

    async def spawn_claude_code(
        self, prompt: str, working_dir: str = "",
        parent_id: str = "", timeout: int = 180,
    ) -> AgentSession:
        """Spawn a Claude Code CLI sub-agent."""
        session = AgentSession(
            type="claude_code", prompt=prompt, parent_id=parent_id,
            metadata={"working_dir": working_dir},
        )
        self._sessions[session.id] = session
        self._emit("agent_spawn", f"claude_code: {prompt[:80]}", agent_id=session.id)
        task = asyncio.create_task(self._run_claude_code(session, working_dir, timeout))
        self._tasks[session.id] = task
        return session

    async def spawn_shell_task(
        self, command: str, parent_id: str = "", timeout: int = 60,
    ) -> AgentSession:
        """Spawn a shell command as background task."""
        session = AgentSession(type="shell_task", prompt=command, parent_id=parent_id)
        self._sessions[session.id] = session
        self._emit("agent_spawn", f"shell: {command[:80]}", agent_id=session.id)
        task = asyncio.create_task(self._run_shell(session, command, timeout))
        self._tasks[session.id] = task
        return session

    # ── Wait/query ──

    async def wait_for(self, session_id: str, timeout: float = 300) -> AgentSession:
        task = self._tasks.get(session_id)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
            except asyncio.TimeoutError:
                session = self._sessions.get(session_id)
                if session and session.status == "running":
                    session.status = "timeout"
                    session.error = f"Wait timed out after {timeout}s"
                    session.completed_at = time.time()
        return self._sessions.get(session_id, AgentSession())

    async def wait_all(self, session_ids: list[str], timeout: float = 300) -> list[AgentSession]:
        tasks = [self._tasks[sid] for sid in session_ids if sid in self._tasks and not self._tasks[sid].done()]
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        return [self._sessions.get(sid, AgentSession()) for sid in session_ids]

    def get_session(self, session_id: str) -> AgentSession | None:
        return self._sessions.get(session_id)

    def get_active_count(self) -> int:
        return sum(1 for s in self._sessions.values() if s.status == "running")

    def get_all_sessions(self) -> list[dict]:
        return [s.to_dict() for s in self._sessions.values()]

    def get_hierarchy(self) -> dict:
        roots, children_map = [], {}
        for s in self._sessions.values():
            d = s.to_dict()
            if s.parent_id:
                children_map.setdefault(s.parent_id, []).append(d)
            else:
                roots.append(d)
        for root in roots:
            root["children"] = children_map.get(root["id"], [])
        return {"sessions": roots, "total": len(self._sessions), "active": self.get_active_count()}

    # ── Runners ──

    async def _run_internal_agent(self, session: AgentSession, timeout: int) -> None:
        """Run a sub-agent with its own LLM agentic loop.

        This is the CORE of multi-agent: the sub-agent gets:
        - Its own LLM conversation (system prompt + focused task)
        - Access to ALL ANIMA tools via tool_use
        - Multi-turn reasoning until done or timeout
        - Result returned as text
        """
        if not self._llm_router or not self._tool_executor or not self._tool_registry:
            session.status = "failed"
            session.error = "AgentManager not wired to LLM (call wire_llm first)"
            session.completed_at = time.time()
            return

        session.status = "running"
        self._emit("agent_running", f"internal agent started: {session.prompt[:60]}", agent_id=session.id)

        try:
            # Build a focused system prompt for this sub-agent
            system_prompt = (
                "You are a focused sub-agent within the ANIMA system. "
                "You have access to tools: shell, read_file, write_file, list_directory, "
                "system_info, get_datetime, web_fetch, save_note.\n\n"
                "Your task is specific and focused. Complete it thoroughly, then respond "
                "with your findings/results as text. Be concise but complete.\n\n"
                "Do NOT greet the user or use personality. Just do the work and report."
            )

            # Build tool schemas
            tool_schemas = []
            # Give sub-agents a subset of tools (no agent spawning to prevent infinite recursion)
            safe_tools = {"shell", "read_file", "write_file", "list_directory",
                         "system_info", "get_datetime", "web_fetch", "save_note"}
            for spec in self._tool_registry.list_tools():
                if spec.name in safe_tools:
                    tool_schemas.append({
                        "name": spec.name,
                        "description": spec.description,
                        "input_schema": spec.parameters or {"type": "object", "properties": {}},
                    })

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": session.prompt},
            ]

            start = time.time()
            final_text = ""

            while time.time() - start < timeout:
                resp = await self._llm_router.call_with_tools(
                    messages=messages, tools=tool_schemas, tier=2,
                )
                if resp is None:
                    session.status = "failed"
                    session.error = "LLM call failed"
                    break

                content = resp.get("content", "")
                tool_calls = resp.get("tool_calls", [])

                if not tool_calls:
                    final_text = content.strip()
                    session.status = "done"
                    break

                # Execute tools
                assistant_blocks = []
                if content:
                    assistant_blocks.append({"type": "text", "text": content})
                for tc in tool_calls:
                    try:
                        inp = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    except (json.JSONDecodeError, TypeError):
                        inp = {}
                    assistant_blocks.append({
                        "type": "tool_use", "id": tc.get("id", tc["name"]),
                        "name": tc["name"], "input": inp,
                    })
                messages.append({"role": "assistant", "content": assistant_blocks})

                tool_results = []
                for tc in tool_calls:
                    name = tc["name"]
                    try:
                        args = json.loads(tc["arguments"]) if isinstance(tc["arguments"], str) else tc["arguments"]
                    except (json.JSONDecodeError, TypeError):
                        args = {}

                    self._emit("agent_tool", f"{session.id}: {name}({json.dumps(args, ensure_ascii=False)[:40]})", agent_id=session.id)

                    result = await self._tool_executor.execute(name, args)
                    result_text = self._format_tool_result(result)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tc.get("id", name),
                        "content": result_text,
                    })

                messages.append({"role": "user", "content": tool_results})
            else:
                # Timeout — ask LLM for a summary
                messages.append({"role": "user", "content": "Time is up. Summarize what you've done and found so far."})
                resp = await self._llm_router.call(messages, tier=2)
                final_text = resp.strip() if resp else "Timed out without result"
                session.status = "done"

            session.result = final_text

        except Exception as e:
            session.status = "failed"
            session.error = str(e)
            log.error("Internal agent %s failed: %s", session.id, e)
        finally:
            session.completed_at = time.time()
            self._emit("agent_done", f"internal: {session.status} ({session.completed_at - session.created_at:.1f}s)", agent_id=session.id)

    async def _run_claude_code(self, session: AgentSession, working_dir: str, timeout: int) -> None:
        session.status = "running"
        self._emit("agent_running", "claude_code started", agent_id=session.id)
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--print", session.prompt,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                cwd=working_dir or None, env=os.environ.copy(),
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            session.result = stdout.decode("utf-8", errors="replace").strip()
            if proc.returncode != 0:
                session.error = stderr.decode("utf-8", errors="replace").strip()
                session.status = "failed"
            else:
                session.status = "done"
        except asyncio.TimeoutError:
            session.status = "timeout"
            session.error = f"Timed out after {timeout}s"
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        except FileNotFoundError:
            session.status = "failed"
            session.error = "Claude Code CLI not found"
        except Exception as e:
            session.status = "failed"
            session.error = str(e)
        finally:
            session.completed_at = time.time()
            self._emit("agent_done", f"claude_code: {session.status}", agent_id=session.id)

    async def _run_shell(self, session: AgentSession, command: str, timeout: int) -> None:
        session.status = "running"
        try:
            env = os.environ.copy()
            python_dir = os.path.dirname(sys.executable)
            if python_dir not in env.get("PATH", ""):
                env["PATH"] = python_dir + os.pathsep + env.get("PATH", "")
            env["PYTHONIOENCODING"] = "utf-8"
            proc = await asyncio.create_subprocess_shell(
                command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            session.result = stdout.decode("utf-8", errors="replace").strip()
            session.status = "done" if proc.returncode == 0 else "failed"
            if proc.returncode != 0:
                session.error = stderr.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            session.status = "timeout"
            session.error = f"Timed out after {timeout}s"
        except Exception as e:
            session.status = "failed"
            session.error = str(e)
        finally:
            session.completed_at = time.time()

    @staticmethod
    def _format_tool_result(result: dict) -> str:
        if not result.get("success"):
            return f"Error: {result.get('error', 'unknown')}"
        raw = result.get("result")
        if isinstance(raw, dict):
            parts = []
            if raw.get("stdout"):
                parts.append(raw["stdout"])
            if raw.get("stderr"):
                parts.append(f"[stderr] {raw['stderr']}")
            if parts:
                return "\n".join(parts)
            return json.dumps(raw, ensure_ascii=False, indent=2)
        if isinstance(raw, str):
            return raw
        if raw is None:
            return "(no output)"
        return str(raw)
