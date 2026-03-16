"""Speech-to-Text — local faster-whisper transcription.

Uses faster-whisper (CTranslate2-based) for fast local transcription.
Downloads the model on first use.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from anima.utils.logging import get_logger

log = get_logger("voice.stt")

# Lazy-loaded model
_stt_model: Any = None
_stt_model_lock = threading.Lock()
_stt_model_failed = False

# Default model size (balance speed vs accuracy)
DEFAULT_MODEL_SIZE = "base"


def _load_model(model_size: str = DEFAULT_MODEL_SIZE) -> Any:
    """Lazy-load the faster-whisper model."""
    global _stt_model, _stt_model_failed

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

            # Use CUDA if available, otherwise CPU
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
                compute_type = "float16" if device == "cuda" else "int8"
            except ImportError:
                device = "cpu"
                compute_type = "int8"

            log.info("Loading Whisper %s model on %s...", model_size, device)
            model = WhisperModel(model_size, device=device, compute_type=compute_type)
            _stt_model = model
            log.info("Whisper model loaded on %s", device)
            return model

        except ImportError:
            log.warning("faster-whisper not installed — STT unavailable")
            _stt_model_failed = True
            return None
        except Exception as e:
            log.warning("Failed to load Whisper model: %s", e)
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
