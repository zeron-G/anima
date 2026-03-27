"""PiDog voice bridge compatibility server.

Exposes the legacy desktop voice API expected by the onboard
``eva_voice_daemon.py`` process:

  - ``GET /health``
  - ``POST /tts``  -> returns MP3 bytes
  - ``POST /stt``  -> accepts ``audio_b64``
  - ``POST /task`` -> forwards complex tasks into ANIMA's event queue
"""

from __future__ import annotations

import base64
import hashlib
import tempfile
import time
from pathlib import Path

from aiohttp import web

from anima.config import data_dir, get
from anima.models.event import Event, EventPriority, EventType
from anima.utils.logging import get_logger
from anima.voice import stt as stt_module
from anima.voice.tts import _clean_text

log = get_logger("voice.bridge")

VOICE_CACHE_DIR = data_dir() / "voice" / "bridge_mp3"


async def synthesize_mp3(
    text: str,
    *,
    voice: str = "",
    use_cache: bool = True,
) -> bytes:
    """Synthesize MP3 bytes via edge-tts with lightweight disk caching."""
    clean = _clean_text(text)
    if not clean:
        return b""
    if len(clean) > 500:
        clean = clean[:500]

    voice_cfg = get("voice_bridge.tts", {})
    voice = voice or str(voice_cfg.get("voice", "zh-CN-XiaoxiaoNeural"))
    rate = str(voice_cfg.get("rate", "+5%"))
    pitch = str(voice_cfg.get("pitch", "+5Hz"))

    cache_key = hashlib.md5(f"{voice}|{rate}|{pitch}|{clean}".encode("utf-8")).hexdigest()[:16]
    cache_path = VOICE_CACHE_DIR / f"{cache_key}.mp3"

    if use_cache and cache_path.exists():
        return cache_path.read_bytes()

    try:
        import edge_tts
    except ImportError as e:
        log.warning("edge-tts not installed — voice bridge TTS unavailable")
        raise RuntimeError("edge-tts not installed") from e

    audio = bytearray()
    communicate = edge_tts.Communicate(clean, voice=voice, rate=rate, pitch=pitch)
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio":
            audio.extend(chunk.get("data", b""))

    data = bytes(audio)
    if use_cache and data:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(data)
    return data


class VoiceBridgeServer:
    """Standalone aiohttp server for PiDog legacy voice compatibility."""

    def __init__(self, hub, host: str = "0.0.0.0", port: int = 9000) -> None:
        self._hub = hub
        self._host = host
        self._port = port
        self._app = self._build_app()
        self._runner: web.AppRunner | None = None
        self._started = False

    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=0)
        app.router.add_get("/health", self._handle_health)
        app.router.add_post("/tts", self._handle_tts)
        app.router.add_post("/stt", self._handle_stt)
        app.router.add_post("/task", self._handle_task)
        return app

    async def start(self) -> None:
        if self._started:
            return
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._started = True
        log.info("Voice bridge running at http://%s:%d", self._host, self._port)

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()
        self._runner = None
        self._started = False

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "timestamp": time.time(),
            "tts_backend": "edge-tts",
            "stt_backend": "faster-whisper",
            "stt_available": stt_module.is_available(),
        })

    async def _handle_tts(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        text = str(data.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "empty text"}, status=400)

        try:
            mp3 = await synthesize_mp3(
                text,
                voice=str(data.get("voice", "")).strip(),
                use_cache=bool(data.get("use_cache", True)),
            )
        except RuntimeError as e:
            return web.json_response({"error": str(e)}, status=503)
        except Exception as e:
            log.error("Voice bridge TTS failed: %s", e)
            return web.json_response({"error": str(e)}, status=500)

        if not mp3:
            return web.json_response({"error": "synthesis failed"}, status=500)

        return web.Response(body=mp3, content_type="audio/mpeg")

    async def _handle_stt(self, request: web.Request) -> web.Response:
        if not stt_module.is_available():
            return web.json_response({"error": "faster-whisper not installed"}, status=503)

        t0 = time.time()
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        audio_b64 = str(data.get("audio_b64", "")).strip()
        language = str(data.get("language", "")).strip() or None
        if not audio_b64:
            return web.json_response({"error": "audio_b64 required"}, status=400)

        try:
            audio = base64.b64decode(audio_b64)
        except Exception:
            return web.json_response({"error": "base64 decode failed"}, status=400)

        if len(audio) < 200:
            return web.json_response({"text": "", "duration_ms": 0})

        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                tmp.write(audio)
                tmp_path = tmp.name
            text = await stt_module.transcribe(tmp_path, language=language)
        except Exception as e:
            log.error("Voice bridge STT failed: %s", e)
            return web.json_response({"error": str(e)}, status=500)
        finally:
            if tmp_path:
                try:
                    Path(tmp_path).unlink(missing_ok=True)
                except Exception:
                    pass

        return web.json_response({
            "text": text,
            "duration_ms": int((time.time() - t0) * 1000),
        })

    async def _handle_task(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        text = str(data.get("text", "")).strip()
        if not text:
            return web.json_response({"error": "text required"}, status=400)
        if not self._hub.event_queue:
            return web.json_response({"error": "backend not ready"}, status=503)

        forwarded = f"[PiDog Voice] {text}"
        await self._hub.event_queue.put(Event(
            type=EventType.USER_MESSAGE,
            payload={"text": forwarded},
            priority=EventPriority.HIGH,
            source="pidog_voice_bridge",
        ))
        self._hub.add_chat_message("user", forwarded)
        return web.json_response({"status": "ok", "message": "任务已转交 Eva"})
