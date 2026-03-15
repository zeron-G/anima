"""Text-to-Speech via Qwen3-TTS server (localhost:9001).

Calls the local Qwen3-TTS FastAPI server's OpenAI-compatible endpoint.
Server runs in WSL: `source ~/qwen3-tts-env/bin/activate && python tools/eva_tts_server.py`

API: POST http://localhost:9001/v1/audio/speech
  {"model": "qwen3-tts", "input": "text", "voice": "eva"}
  → audio/wav response
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("voice.tts")

TTS_API = "http://localhost:9001/v1/audio/speech"
VOICE_DIR = data_dir() / "voice"


def _clean_text(text: str) -> str:
    """Strip markdown formatting for TTS."""
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`[^`]+`", "", text)
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"^[\s]*[-*]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"-{3,}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _synthesize_sync(clean: str, out_path: Path) -> bool:
    """Call Qwen3-TTS server synchronously (runs in thread)."""
    import urllib.request
    import json

    body = json.dumps({
        "model": "qwen3-tts",
        "input": clean,
        "voice": "eva",
    }).encode("utf-8")

    req = urllib.request.Request(
        TTS_API,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            audio_data = resp.read()
            if len(audio_data) > 100:
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(audio_data)
                return True
            return False
    except Exception as e:
        log.error("Qwen3-TTS request failed: %s", e)
        return False


async def synthesize(
    text: str,
    emotion: str = "",
) -> Path | None:
    """Generate TTS audio via Qwen3-TTS server.

    Returns path to wav file or None on failure.
    Uses caching by content hash.
    """
    clean = _clean_text(text)
    if not clean or len(clean) < 2:
        return None

    if len(clean) > 500:
        clean = clean[:500]

    cache_key = hashlib.md5(clean.encode()).hexdigest()[:12]
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = VOICE_DIR / f"tts_{cache_key}.wav"

    if out_path.exists():
        return out_path

    ok = await asyncio.get_event_loop().run_in_executor(
        None, _synthesize_sync, clean, out_path
    )

    if ok:
        log.debug("TTS synthesized: %s (%d bytes)", out_path.name, out_path.stat().st_size)
        return out_path

    try:
        out_path.unlink(missing_ok=True)
    except Exception:
        pass
    return None


def cleanup_cache(max_files: int = 100) -> None:
    """Remove old TTS cache files."""
    if not VOICE_DIR.exists():
        return
    files = sorted(VOICE_DIR.glob("tts_*.*"), key=lambda f: f.stat().st_mtime)
    if len(files) > max_files:
        for f in files[: len(files) - max_files]:
            try:
                f.unlink()
            except Exception:
                pass
