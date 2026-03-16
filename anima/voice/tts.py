"""Text-to-Speech via Qwen3-TTS (local PyTorch CUDA).

Model: Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice (auto-downloaded from HuggingFace)
Runs entirely in-process. No HTTP server, no WSL.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from pathlib import Path
from typing import Any

from anima.config import data_dir
from anima.utils.logging import get_logger

log = get_logger("voice.tts")

VOICE_DIR = data_dir() / "voice"
MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"

_model: Any = None
_model_lock = threading.Lock()
_model_failed = False


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


def _load_model() -> Any:
    """Lazy-load Qwen3-TTS model."""
    global _model, _model_failed

    if _model is not None:
        return _model
    if _model_failed:
        return None

    with _model_lock:
        if _model is not None:
            return _model
        if _model_failed:
            return None

        try:
            import torch
            from qwen_tts import Qwen3TTSModel

            log.info("Loading Qwen3-TTS model: %s ...", MODEL_ID)

            model = Qwen3TTSModel.from_pretrained(
                MODEL_ID,
                device_map="cuda:0",
                dtype=torch.bfloat16,
            )
            _model = model
            log.info("Qwen3-TTS loaded on CUDA")
            return model

        except Exception as e:
            log.error("Failed to load Qwen3-TTS: %s", e)
            _model_failed = True
            return None


def _synthesize_sync(clean: str, out_path: Path) -> bool:
    """Generate speech synchronously (runs in thread)."""
    model = _load_model()
    if model is None:
        return False

    try:
        import soundfile as sf

        wavs, sr = model.generate_custom_voice(
            text=clean,
            language="Chinese",
            speaker="Vivian",
            instruct="用温柔甜美的少女声音说话",
        )

        if not wavs or len(wavs) == 0:
            return False

        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), wavs[0], sr)
        return out_path.exists() and out_path.stat().st_size > 100

    except Exception as e:
        log.error("Qwen3-TTS synthesis failed: %s", e)
        return False


async def synthesize(
    text: str,
    emotion: str = "",
) -> Path | None:
    """Generate TTS audio via Qwen3-TTS.

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
        log.debug("TTS: %s (%d bytes)", out_path.name, out_path.stat().st_size)
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
