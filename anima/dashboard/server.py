"""Dashboard web server — aiohttp + WebSocket for real-time monitoring."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from aiohttp import web, WSMsgType

from anima.config import get, project_root
from anima.dashboard.hub import DashboardHub
from anima.dashboard.page import DASHBOARD_HTML
from anima.models.event import Event, EventType, EventPriority
from anima.utils.logging import get_logger

log = get_logger("dashboard")


class DashboardServer:
    """Web dashboard for ANIMA monitoring and control."""

    def __init__(self, hub: DashboardHub, host: str = "0.0.0.0", port: int = 8420) -> None:
        self._hub = hub
        self._host = host
        self._port = port
        self._auth_token = get("dashboard.auth.token", "")
        self._app = web.Application(client_max_size=0)  # No upload size limit
        self._ws_clients: list[web.WebSocketResponse] = []
        self._runner: web.AppRunner | None = None
        self._push_task: asyncio.Task | None = None

        self._app.router.add_get("/", self._handle_index)
        self._app.router.add_get("/ws", self._handle_ws)
        self._app.router.add_post("/api/chat", self._handle_chat)
        self._app.router.add_post("/api/chat/stream", self._handle_chat_stream)
        self._app.router.add_post("/api/chat/upload", self._handle_chat_upload)
        self._app.router.add_post("/api/control", self._handle_control)
        self._app.router.add_post("/api/config", self._handle_config)
        self._app.router.add_post("/api/upload", self._handle_upload)
        self._app.router.add_get("/api/uploads", self._handle_list_uploads)
        self._app.router.add_post("/api/debug", self._handle_debug)
        self._app.router.add_post("/api/tts", self._handle_tts)
        self._app.router.add_post("/api/stt", self._handle_stt)
        self._app.router.add_get("/api/voice/{filename}", self._handle_voice_file)
        # Trace endpoint — returns recent cognitive loop traces for debugging
        self._app.router.add_get("/api/traces", self._handle_traces)
        # v1 API — structured RESTful endpoints for Vue SPA / Tauri / external clients
        from anima.api.router import APIRouter
        api = APIRouter(hub)
        api.register(self._app)
        # Static files for Live2D model + SDK
        static_dir = Path(__file__).parent / "static"
        if static_dir.exists():
            self._app.router.add_static("/static/", static_dir)

        # Desktop frontend (VRM/Live2D + chat window)
        desktop_dir = Path(__file__).parent.parent / "desktop" / "frontend"
        if desktop_dir.exists():
            self._app.router.add_get("/desktop", self._handle_desktop)
            self._app.router.add_get("/desktop/", self._handle_desktop)
            self._app.router.add_static("/desktop/static/", desktop_dir)

    async def start(self) -> None:
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._push_task = asyncio.create_task(self._push_loop())
        from anima.network.discovery import get_local_ip
        log.info("Dashboard running at http://%s:%d", get_local_ip(), self._port)

    async def stop(self) -> None:
        if self._push_task:
            self._push_task.cancel()
        for ws in self._ws_clients:
            await ws.close()
        if self._runner:
            await self._runner.cleanup()
        log.info("Dashboard stopped.")

    # ── Auth ──

    def _check_auth(self, request) -> bool:
        """Check if request is authenticated. Returns True if auth passes."""
        if not self._auth_token:
            return True  # Auth disabled
        # Check Authorization header
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {self._auth_token}":
            return True
        # Check query param (for WebSocket)
        if request.query.get("token") == self._auth_token:
            return True
        return False

    # ── Routes ──

    async def _handle_index(self, request: web.Request) -> web.Response:
        return web.Response(text=DASHBOARD_HTML, content_type="text/html")

    async def _handle_desktop(self, request: web.Request) -> web.Response:
        desktop_index = Path(__file__).parent.parent / "desktop" / "frontend" / "index.html"
        if desktop_index.exists():
            return web.FileResponse(desktop_index)
        return web.Response(text="Desktop frontend not found", status=404)

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._ws_clients.append(ws)
        log.debug("WebSocket client connected (%d total)", len(self._ws_clients))

        # Send initial snapshot
        await ws.send_json(self._hub.get_full_snapshot())

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    pass  # Client messages handled via REST
                elif msg.type == WSMsgType.ERROR:
                    break
        finally:
            self._ws_clients.remove(ws)
        return ws

    async def _handle_chat(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except (UnicodeDecodeError, ValueError):
            # Handle non-UTF-8 encoded bodies (e.g. Windows GBK terminals)
            raw = await request.read()
            try:
                data = __import__("json").loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return web.json_response({"error": "invalid JSON or encoding"}, status=400)
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"error": "empty message"}, status=400)

        # Record in hub
        self._hub.add_chat_message("user", text)

        # Push to ANIMA's event queue
        if self._hub.event_queue:
            await self._hub.event_queue.put(Event(
                type=EventType.USER_MESSAGE,
                payload={"text": text},
                priority=EventPriority.CRITICAL,  # User messages jump the queue
                source="dashboard",
            ))

        return web.json_response({"ok": True})

    async def _handle_chat_stream(self, request: web.Request) -> web.StreamResponse:
        """SSE streaming chat — sends events as Eva processes the message."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except (UnicodeDecodeError, ValueError):
            raw = await request.read()
            try:
                data = __import__("json").loads(raw.decode("utf-8", errors="replace"))
            except Exception:
                return web.json_response({"error": "invalid JSON or encoding"}, status=400)
        text = data.get("text", "").strip()
        if not text:
            return web.json_response({"error": "empty"}, status=400)

        resp = web.StreamResponse()
        resp.headers["Content-Type"] = "text/event-stream"
        resp.headers["Cache-Control"] = "no-cache"
        resp.headers["X-Accel-Buffering"] = "no"
        await resp.prepare(request)

        async def send_sse(event: str, data: dict):
            payload = f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
            await resp.write(payload.encode("utf-8"))

        # Record in hub
        self._hub.add_chat_message("user", text)
        await send_sse("status", {"state": "received"})

        # Create a queue to receive status updates from cognitive loop
        stream_queue: asyncio.Queue = asyncio.Queue()
        stream_id = f"stream_{id(resp)}"

        # Register stream listener on hub
        self._hub.register_stream(stream_id, stream_queue)

        # Push to ANIMA's event queue
        if self._hub.event_queue:
            await self._hub.event_queue.put(Event(
                type=EventType.USER_MESSAGE,
                payload={"text": text, "stream_id": stream_id},
                priority=EventPriority.CRITICAL,
                source="dashboard",
            ))

        # Stream events until done
        try:
            while True:
                try:
                    event = await asyncio.wait_for(stream_queue.get(), timeout=120)
                    if event is None:  # sentinel for done
                        break
                    await send_sse(event["type"], event["data"])
                    if event["type"] == "done":
                        break
                except asyncio.TimeoutError:
                    await send_sse("error", {"message": "timeout"})
                    break
        finally:
            self._hub.unregister_stream(stream_id)

        return resp

    async def _handle_chat_upload(self, request: web.Request) -> web.Response:
        """Handle file upload with chat message."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        reader = await request.multipart()
        text = ""
        saved = []

        upload_dir = Path(project_root()) / "data" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        async for field in reader:
            if field.name == "text":
                text = (await field.read(decode=True)).decode("utf-8")
            elif field.name == "files":
                data = await field.read(decode=False)
                path = upload_dir / field.filename
                path.write_bytes(data)
                saved.append(str(path))

        # Add to hub with file references
        file_note = "\n".join(f"[Attached: {f}]" for f in saved)
        full_text = f"{text}\n{file_note}" if saved else text

        self._hub.add_chat_message("user", full_text)

        if self._hub.event_queue:
            await self._hub.event_queue.put(Event(
                type=EventType.USER_MESSAGE,
                payload={"text": full_text, "files": saved},
                priority=EventPriority.CRITICAL,
                source="dashboard",
            ))

        return web.json_response({"ok": True, "files": len(saved)})

    async def _handle_control(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        data = await request.json()
        action = data.get("action", "")

        if action == "shutdown":
            if self._hub.event_queue:
                await self._hub.event_queue.put(Event(
                    type=EventType.SHUTDOWN,
                    priority=EventPriority.CRITICAL,
                    source="dashboard",
                ))
            return web.json_response({"ok": True, "action": "shutdown"})

        if action == "pause_heartbeat":
            if self._hub.heartbeat and self._hub.heartbeat._running:
                await self._hub.heartbeat.stop()
            return web.json_response({"ok": True, "action": "paused"})

        if action == "resume_heartbeat":
            if self._hub.heartbeat and not self._hub.heartbeat._running:
                await self._hub.heartbeat.start()
            return web.json_response({"ok": True, "action": "resumed"})

        if action == "clear_working_memory":
            if self._hub.working_memory:
                self._hub.working_memory.clear()
            return web.json_response({"ok": True, "action": "cleared"})

        if action == "restart":
            import sys
            log.info("Restart requested from dashboard")
            # M-11 fix: use sys.exit instead of os.execv (which skips all cleanup)
            asyncio.get_event_loop().call_later(1.0, lambda: sys.exit(42))  # 42 = restart exit code
            return web.json_response({"ok": True, "action": "restarting"})

        return web.json_response({"error": f"unknown action: {action}"}, status=400)

    async def _handle_config(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        data = await request.json()
        key = data.get("key", "")
        value = data.get("value")
        if not key:
            return web.json_response({"error": "missing key"}, status=400)

        try:
            self._hub.update_config(key, value)
            log.info("Config updated: %s = %s", key, value)
            return web.json_response({"ok": True, "key": key, "value": value})
        except Exception as e:
            log.error("Config update failed: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    # ── File upload ──

    async def _handle_upload(self, request: web.Request) -> web.Response:
        """Handle file upload via multipart form data."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        uploads_dir = project_root() / "data" / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)

        reader = await request.multipart()
        files_saved = []

        while True:
            part = await reader.next()
            if part is None:
                break
            if part.name == "file":
                filename = part.filename or f"upload_{int(time.time())}"
                # Sanitize filename
                safe_name = Path(filename).name
                dest = uploads_dir / safe_name
                with open(dest, "wb") as f:
                    while True:
                        chunk = await part.read_chunk()
                        if not chunk:
                            break
                        f.write(chunk)
                files_saved.append({"name": safe_name, "path": str(dest), "size": dest.stat().st_size})
                log.info("File uploaded: %s (%d bytes)", safe_name, dest.stat().st_size)

                # Notify ANIMA about the upload
                if self._hub.event_queue:
                    await self._hub.event_queue.put(Event(
                        type=EventType.USER_MESSAGE,
                        payload={
                            "text": f"I just uploaded a file: `{safe_name}` (saved to `{dest}`). Please acknowledge it.",
                        },
                        priority=EventPriority.HIGH,
                        source="dashboard_upload",
                    ))

        if not files_saved:
            return web.json_response({"error": "no files received"}, status=400)
        return web.json_response({"ok": True, "files": files_saved})

    async def _handle_list_uploads(self, request: web.Request) -> web.Response:
        """List files in the uploads directory."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        uploads_dir = project_root() / "data" / "uploads"
        if not uploads_dir.exists():
            return web.json_response({"files": []})
        files = []
        for f in sorted(uploads_dir.iterdir()):
            if f.is_file():
                files.append({"name": f.name, "path": str(f), "size": f.stat().st_size})
        return web.json_response({"files": files})

    # ── TTS ──

    async def _handle_tts(self, request: web.Request) -> web.Response:
        """Synthesize text to speech. Returns URL to audio file."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
            text = data.get("text", "").strip()
            if not text:
                return web.json_response({"error": "empty text"}, status=400)

            emotion = ""
            try:
                if self._hub.emotion_state:
                    dominant = self._hub.emotion_state.dominant()
                    emotion = dominant if dominant != "engagement" else ""
            except Exception as e:
                log.debug("_handle_tts: %s", e)

            # Run TTS in thread to avoid event loop conflicts with edge-tts
            from anima.voice.tts import synthesize, _clean_text
            clean = _clean_text(text)
            if not clean or len(clean) < 2:
                return web.json_response({"error": "text too short"}, status=400)

            path = await synthesize(clean, emotion=emotion)
            if path and path.exists():
                return web.json_response({"ok": True, "url": f"/api/voice/{path.name}"})
            return web.json_response({"error": "synthesis failed"}, status=500)
        except Exception as e:
            log.error("TTS handler error: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_debug(self, request: web.Request) -> web.Response:
        """Receive debug logs from frontend."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        data = await request.json()
        level = data.get("level", "info").upper()
        msg = data.get("msg", "")
        source = data.get("source", "frontend")
        log_fn = getattr(log, level.lower(), log.info)
        log_fn("[%s] %s", source, msg)
        return web.json_response({"ok": True})

    async def _handle_stt(self, request: web.Request) -> web.Response:
        """Transcribe uploaded audio via local Whisper."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            import tempfile
            reader = await request.multipart()
            part = await reader.next()
            if part is None or part.name != "audio":
                return web.json_response({"error": "no audio part"}, status=400)

            # Save to temp file
            suffix = ".webm"
            if part.filename:
                suffix = Path(part.filename).suffix or suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                while True:
                    chunk = await part.read_chunk()
                    if not chunk:
                        break
                    tmp.write(chunk)
                tmp_path = tmp.name

            # Transcribe
            from anima.voice.stt import transcribe
            text = await transcribe(tmp_path)

            # Cleanup temp file
            try:
                Path(tmp_path).unlink()
            except Exception as e:
                log.debug("server: %s", e)

            if text:
                return web.json_response({"ok": True, "text": text})
            return web.json_response({"error": "transcription empty"}, status=500)
        except ImportError:
            return web.json_response({"error": "faster-whisper not installed"}, status=501)
        except Exception as e:
            log.error("STT handler error: %s", e)
            return web.json_response({"error": str(e)}, status=500)

    async def _handle_voice_file(self, request: web.Request) -> web.Response:
        """Serve a generated voice audio file."""
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        from anima.config import data_dir
        filename = request.match_info["filename"]
        # Security: only serve from voice dir, no path traversal
        if ".." in filename or "/" in filename or "\\" in filename:
            return web.Response(status=403)
        path = data_dir() / "voice" / filename
        if not path.exists():
            return web.Response(status=404)
        ct = "audio/wav" if path.suffix == ".wav" else "audio/mpeg"
        return web.FileResponse(path, headers={"Content-Type": ct})

    # ── Traces ──

    async def _handle_traces(self, request: web.Request) -> web.Response:
        """Return recent cognitive loop traces for debugging.

        Query params:
          - limit: max number of traces (default 50)
          - since: unix timestamp to filter from (optional)

        Reads from hub.get_traces() if available, otherwise returns
        an empty list.  This endpoint is intended for the dashboard
        to display tool-use timelines, LLM call latencies, and
        event processing history.
        """
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        limit = int(request.query.get("limit", "50"))
        since = float(request.query.get("since", "0"))

        traces: list[dict] = []
        if hasattr(self._hub, "get_traces"):
            traces = self._hub.get_traces(limit=limit, since=since)

        return web.json_response({"traces": traces, "count": len(traces)})

    # ── v1 API ──

    async def _handle_v1_chat(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)
        message = data.get("message", "")
        if not message:
            return web.json_response({"error": "message required"}, status=400)

        # Push to event queue (same as existing /api/chat)
        correlation_id = f"v1_{id(request)}_{int(time.time())}"
        await self._hub.event_queue.put(Event(
            type=EventType.USER_MESSAGE,
            payload={"text": message},
            priority=EventPriority.NORMAL,
            source="api_v1",
        ))
        return web.json_response({"status": "queued", "correlation_id": correlation_id})

    async def _handle_v1_status(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        snapshot = self._hub.get_full_snapshot()
        return web.json_response({
            "status": "running",
            "uptime_s": snapshot.get("uptime", 0),
            "emotion": snapshot.get("emotion", {}),
            "queue_depth": snapshot.get("queue_depth", 0),
            "idle": snapshot.get("idle", {}),
            "llm": snapshot.get("llm_status", {}),
        })

    async def _handle_v1_emotion(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        snapshot = self._hub.get_full_snapshot()
        return web.json_response(snapshot.get("emotion", {}))

    async def _handle_v1_memory_search(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        query = request.query.get("q", "")
        limit = int(request.query.get("limit", "5"))
        if not query:
            return web.json_response({"error": "q parameter required"}, status=400)
        results = await self._hub._memory_store.search_memories_async(query=query, limit=limit)
        # Strip internal fields, return clean JSON
        clean = [{"id": r.get("id"), "content": r.get("content", "")[:500],
                  "type": r.get("type"), "importance": r.get("importance"),
                  "created_at": r.get("created_at")} for r in results]
        return web.json_response({"results": clean, "count": len(clean)})

    async def _handle_v1_sessions(self, request: web.Request) -> web.Response:
        if not self._check_auth(request):
            return web.json_response({"error": "unauthorized"}, status=401)
        sm = self._hub.session_manager
        if not sm:
            return web.json_response({"active": 0, "sessions": []})
        return web.json_response({
            "active": sm.active_count,
            "sessions": sm.list_sessions(),
        })

    # ── WebSocket push loop ──

    async def _push_loop(self) -> None:
        """Push state to all WebSocket clients every 2 seconds."""
        while True:
            try:
                await asyncio.sleep(2)
                if not self._ws_clients:
                    continue
                snapshot = self._hub.get_full_snapshot()
                dead = []
                for ws in self._ws_clients:
                    try:
                        await ws.send_json(snapshot)
                    except Exception:
                        dead.append(ws)
                for ws in dead:
                    self._ws_clients.remove(ws)
            except asyncio.CancelledError:
                return
            except Exception as e:
                log.error("Dashboard push error: %s", e)
