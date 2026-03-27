"""Speech-to-Text — local faster-whisper transcription.

Uses faster-whisper (CTranslate2-based) for fast local transcription.
Downloads the model on first use.
"""

from __future__ import annotations

import threading
import sys
from pathlib import Path
from typing import Any

from anima.config import get
from anima.utils.logging import get_logger

log = get_logger("voice.stt")

# Lazy-loaded model
_stt_model: Any = None
_stt_model_lock = threading.Lock()
_stt_model_failed = False

# Default model size (balance speed vs accuracy)
DEFAULT_MODEL_SIZE = "base"


def _load_model(model_size: str | None = None) -> Any:
    """Lazy-load the faster-whisper model."""
    global _stt_model, _stt_model_failed
    model_size = model_size or str(get("voice_bridge.stt.model_size", DEFAULT_MODEL_SIZE))

    if _stt_model is not None:
        return _stt_model
    if _stt_model_failed:
        return None

    with _stt_model_lock:
        if _stt_model is not None:
            return _stt_model
        if _stt_model_failed:
            return None

        try:
            from faster_whisper import WhisperModel

            backends: list[tuple[str, str]] = []
            if sys.platform == "win32":
                backends.append(("cpu", "int8"))
            try:
                import torch
                if torch.cuda.is_available() and sys.platform != "win32":
                    backends.append(("cuda", "float16"))
            except ImportError:
                pass
            if ("cpu", "int8") not in backends:
                backends.append(("cpu", "int8"))

            last_error: Exception | None = None
            for device, compute_type in backends:
                try:
                    log.info(
                        "Loading Whisper %s model on %s (%s)...",
                        model_size,
                        device,
                        compute_type,
                    )
                    model = WhisperModel(model_size, device=device, compute_type=compute_type)
                    _stt_model = model
                    log.info("Whisper model loaded on %s", device)
                    return model
                except Exception as e:
                    last_error = e
                    log.warning("Whisper load failed on %s: %s", device, e)

        except ImportError:
            log.warning("faster-whisper not installed — STT unavailable")
            _stt_model_failed = True
            return None

        if last_error is not None:
            log.warning("Failed to load Whisper model: %s", last_error)
        _stt_model_failed = True
        return None


def transcribe_sync(audio_path: str | Path, language: str | None = None) -> str:
    """Transcribe audio file to text synchronously.

    Args:
        audio_path: Path to audio file (WAV, MP3, WebM, etc.)
        language: Optional language code (e.g., "zh", "en"). Auto-detect if None.

    Returns:
        Transcribed text, or empty string on failure.
    """
    model = _load_model()
    if model is None:
        return ""

    try:
        segments, info = model.transcribe(
            str(audio_path),
            language=language,
            beam_size=5,
            vad_filter=True,
        )

        text_parts = []
        for segment in segments:
            text_parts.append(segment.text.strip())

        result = " ".join(text_parts).strip()
        log.debug("STT result (%s, %.1fs): %s",
                  info.language, info.duration, result[:100])
        return result

    except Exception as e:
        log.error("STT transcription failed: %s", e)
        return ""


async def transcribe(audio_path: str | Path, language: str | None = None) -> str:
    """Transcribe audio file to text asynchronously."""
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None, transcribe_sync, audio_path, language
    )


def is_available() -> bool:
    """Check if STT is available (faster-whisper installed)."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
