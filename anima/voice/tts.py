"""Text-to-Speech via Qwen3-TTS voice clone (local PyTorch CUDA).

Uses Eva's reference voice (data/voice/eva_reference_voice.wav) for cloning.
Model: Qwen3-TTS Base (supports generate_voice_clone with ref_audio).
Runs entirely in-process. No HTTP server, no WSL.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import threading
from pathlib import Path
from typing import Any

from anima.config import data_dir, local_get, project_root
from anima.utils.logging import get_logger

log = get_logger("voice.tts")

VOICE_DIR = data_dir() / "voice"
REF_VOICE = data_dir() / "voice" / "eva_reference_voice.wav"
DEFAULT_MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"  # Base model for voice clone

_model: Any = None
_model_lock = threading.Lock()
_model_failed = False
_voice_prompt: Any = None  # Cached VoiceClonePromptItem


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
    """Lazy-load Qwen3-TTS Base model for voice cloning."""
    global _model, _model_failed, _voice_prompt

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

            model_id = local_get("tts.model_id", DEFAULT_MODEL_ID)
            log.info("Loading Qwen3-TTS: %s ...", model_id)

            model = Qwen3TTSModel.from_pretrained(
                model_id,
                device_map="cuda:0",
                dtype=torch.bfloat16,
            )
            _model = model

            # Pre-build voice clone prompt from reference audio (cached for reuse)
            ref_path = str(REF_VOICE)
            if REF_VOICE.exists():
                log.info("Building voice clone prompt from %s", REF_VOICE.name)
                _voice_prompt = model.create_voice_clone_prompt(
                    ref_audio=ref_path,
                    x_vector_only_mode=True,  # Speaker embedding only, faster
                )
                log.info("Qwen3-TTS loaded with Eva voice clone")
            else:
                log.warning("Reference voice not found: %s — will use default voice", ref_path)
                log.info("Qwen3-TTS loaded (no voice clone)")

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

        language = local_get("tts.language", "Chinese")

        if _voice_prompt is not None:
            # Voice clone mode — use Eva's reference voice
            wavs, sr = model.generate_voice_clone(
                text=clean,
                language=language,
                voice_clone_prompt=_voice_prompt,
            )
        else:
            # Fallback — no reference voice, use custom voice if available
            try:
                speaker = local_get("tts.speaker", "Vivian")
                instruct = local_get("tts.instruct", "")
                wavs, sr = model.generate_custom_voice(
                    text=clean,
                    language=language,
                    speaker=speaker,
                    instruct=instruct,
                )
            except (AttributeError, ValueError):
                # Base model doesn't have generate_custom_voice
                wavs, sr = model.generate_voice_clone(
                    text=clean,
                    language=language,
                    ref_audio=str(REF_VOICE) if REF_VOICE.exists() else None,
                    x_vector_only_mode=True,
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
    """Generate TTS audio with Eva's cloned voice.

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
    """Remove old TTS cache files (keeps reference voice)."""
    if not VOICE_DIR.exists():
        return
    files = sorted(
        [f for f in VOICE_DIR.glob("tts_*.*") if f.name != "eva_reference_voice.wav"],
        key=lambda f: f.stat().st_mtime,
    )
    if len(files) > max_files:
        for f in files[: len(files) - max_files]:
            try:
                f.unlink()
            except Exception:
                pass
