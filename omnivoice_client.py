"""Локальная озвучка через OmniVoice (k2-fsa/OmniVoice)."""

from __future__ import annotations

import io
import logging
import os
import threading
from typing import Any

from tts_voices import resolve_omnivoice_instruct

logger = logging.getLogger(__name__)

_model: Any = None
_model_id: str | None = None
_lock = threading.Lock()


def _pick_device() -> str:
    import torch

    if torch.cuda.is_available():
        return "cuda"
    if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def is_omnivoice_ready(model_id: str | None = None) -> bool:
    """Модель уже в памяти — без многогигабайтной загрузки при первом голосовом."""
    if _model is None:
        return False
    if model_id is None:
        return True
    return _model_id == model_id


def _load_model(model_id: str) -> Any:
    global _model, _model_id
    with _lock:
        if _model is not None and _model_id == model_id:
            return _model
        import torch
        from omnivoice.models.omnivoice import OmniVoice

        device = _pick_device()
        logger.info("Загрузка OmniVoice %s на %s (первый раз может занять минуты)...", model_id, device)
        _model = OmniVoice.from_pretrained(model_id, device_map=device, dtype=torch.float16)
        _model_id = model_id
        logger.info("OmniVoice готова (sample_rate=%s)", getattr(_model, "sampling_rate", "?"))
        return _model


def synthesize_omnivoice_wav(
    text: str,
    *,
    model_id: str,
    voice_id: str,
    instruct: str = "",
    ref_audio: str = "",
    ref_text: str = "",
    language: str = "Russian",
) -> bytes:
    if not text.strip():
        raise ValueError("Пустой текст для OmniVoice")

    try:
        import soundfile as sf
    except ImportError as exc:
        raise RuntimeError(
            "omnivoice не установлен. Выполни: python -m pip install omnivoice"
        ) from exc

    model = _load_model(model_id)
    num_step = max(8, int(os.getenv("OMNIVOICE_NUM_STEP", "24")))
    guidance_scale = float(os.getenv("OMNIVOICE_GUIDANCE", "2.0"))

    generate_kwargs: dict[str, Any] = {
        "text": text,
        "language": language or "Russian",
        "num_step": num_step,
        "guidance_scale": guidance_scale,
    }

    ref_path = ref_audio.strip()
    if ref_path and os.path.isfile(ref_path):
        generate_kwargs["ref_audio"] = ref_path
        generate_kwargs["ref_text"] = ref_text.strip() or text[:120]
        mode = "clone"
    else:
        style = instruct.strip() or resolve_omnivoice_instruct(voice_id)
        generate_kwargs["instruct"] = style
        mode = "design"

    logger.info(
        "OmniVoice %s: %d симв., steps=%d, voice=%r",
        mode,
        len(text),
        num_step,
        voice_id,
    )

    with _lock:
        audios = model.generate(**generate_kwargs)

    if not audios:
        raise RuntimeError("OmniVoice вернула пустой результат")

    buffer = io.BytesIO()
    sf.write(buffer, audios[0], model.sampling_rate, format="WAV")
    wav_bytes = buffer.getvalue()
    if not wav_bytes:
        raise RuntimeError("OmniVoice не записала WAV")
    logger.info("OmniVoice WAV: %d байт", len(wav_bytes))
    return wav_bytes
