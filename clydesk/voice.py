"""Local speech-to-text via faster-whisper (lazy-loaded, GPU if available).

Optional feature: requires `pip install faster-whisper`. The first
transcription downloads the model (~500MB for 'small')."""

import asyncio
import os
import tempfile
import threading

_model = None
_load_error: str | None = None
_model_lock = threading.Lock()


def _get_model(size: str):
    global _model, _load_error
    if _model is not None or _load_error is not None:
        return _model
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        _load_error = ("faster-whisper is not installed — run: "
                       ".venv/bin/pip install faster-whisper")
        return None
    try:
        _model = WhisperModel(size, device="cuda", compute_type="int8_float16")
    except Exception:
        try:
            _model = WhisperModel(size, device="cpu", compute_type="int8")
        except Exception as e:
            _load_error = f"whisper model load failed: {e}"
    return _model


def _force_cpu(size: str):
    """CUDA can fail at encode time (missing libcublas); rebuild on CPU."""
    global _model
    from faster_whisper import WhisperModel
    _model = WhisperModel(size, device="cpu", compute_type="int8")
    return _model


def _transcribe_blocking(audio_bytes: bytes, size: str) -> str:
    with _model_lock:  # two quick recordings must not load the model twice
        model = _get_model(size)
    if model is None:
        raise RuntimeError(_load_error or "model unavailable")
    with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
        f.write(audio_bytes)
        path = f.name
    try:
        try:
            segments, _info = model.transcribe(path, language="en", beam_size=1)
            return " ".join(seg.text.strip() for seg in segments).strip()
        except RuntimeError as e:
            if "libcublas" not in str(e) and "CUDA" not in str(e):
                raise
            model = _force_cpu(size)
            segments, _info = model.transcribe(path, language="en", beam_size=1)
            return " ".join(seg.text.strip() for seg in segments).strip()
    finally:
        os.unlink(path)


async def transcribe(audio_bytes: bytes, size: str = "small") -> str:
    """Transcribe recorded audio without blocking the event loop."""
    return await asyncio.to_thread(_transcribe_blocking, audio_bytes, size)
