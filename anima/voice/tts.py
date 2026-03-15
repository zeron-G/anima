"""Text-to-Speech via Microsoft Edge TTS (free, no API key).

Generates audio from text using edge-tts. Supports Chinese voices.
Audio files are cached in data/voice/ and served via dashboard API.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("voice.tts")

# Default voice — Chinese female (natural, warm)
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"
VOICE_DIR = data_dir() / "voice"

# Emotion → voice style mapping (XiaoxiaoNeural supports styles)
EMOTION_STYLES = {
    "happy": "cheerful",
    "sad": "sad",
    "angry": "angry",
    "fearful": "fearful",
    "calm": "calm",
    "excited": "cheerful",
    "gentle": "gentle",
    "serious": "serious",
}


def _clean_text(text: str) -> str:
    """Strip markdown formatting for TTS."""
    # Remove code blocks
    text = re.sub(r"```[\s\S]*?```", "", text)
    # Remove inline code
    text = re.sub(r"`[^`]+`", "", text)
    # Remove markdown bold/italic
    text = re.sub(r"\*{1,3}([^*]+)\*{1,3}", r"\1", text)
    # Remove headers
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Remove links [text](url) → text
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    # Remove bullet points
    text = re.sub(r"^[\s]*[-*]\s+", "", text, flags=re.MULTILINE)
    # Remove table formatting
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"-{3,}", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _synthesize_sync(clean: str, voice: str, out_path: Path) -> bool:
    """Run edge-tts in its own event loop (thread-safe)."""
    import asyncio
    import edge_tts

    async def _do():
        communicate = edge_tts.Communicate(clean, voice)
        await communicate.save(str(out_path))

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_do())
        return out_path.exists() and out_path.stat().st_size > 0
    except Exception as e:
        log.error("TTS synthesis failed: %s", e)
        return False
    finally:
        loop.close()


async def synthesize(
    text: str,
    voice: str = DEFAULT_VOICE,
    emotion: str = "",
) -> Path | None:
    """Generate TTS audio file. Returns path to mp3 or None on failure.

    Runs edge-tts in a separate thread with its own event loop
    to avoid conflicts with the main aiohttp loop.
    """
    try:
        import edge_tts
    except ImportError:
        log.warning("edge-tts not installed")
        return None

    clean = _clean_text(text)
    if not clean or len(clean) < 2:
        return None

    if len(clean) > 500:
        clean = clean[:500] + "..."

    cache_key = hashlib.md5(f"{clean}:{voice}:{emotion}".encode()).hexdigest()[:12]
    VOICE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = VOICE_DIR / f"tts_{cache_key}.mp3"

    if out_path.exists():
        return out_path

    import asyncio
    ok = await asyncio.get_event_loop().run_in_executor(
        None, _synthesize_sync, clean, voice, out_path
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
    files = sorted(VOICE_DIR.glob("tts_*.mp3"), key=lambda f: f.stat().st_mtime)
    if len(files) > max_files:
        for f in files[: len(files) - max_files]:
            try:
                f.unlink()
            except Exception:
                pass
