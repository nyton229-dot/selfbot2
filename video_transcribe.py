"""Распознавание речи из видео VK через Whisper API."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import requests

from ffmpeg_util import extract_audio_wav
from vk_media import download_video_file, get_video_items

logger = logging.getLogger(__name__)

MAX_TRANSCRIBE_SECONDS = 45
MAX_AUDIO_BYTES = 24 * 1024 * 1024

_SPEECH_HINTS = (
    "говор",
    "сказ",
    "расшифр",
    "реч",
    "звук",
    "аудио",
    "слыш",
    "что там",
    "что в видео",
    "о чём",
    "о чем",
    "текст видео",
    "слова",
)


def should_transcribe_video(prompt: str, *, mode: str = "auto") -> bool:
    normalized = mode.strip().casefold()
    if normalized in ("0", "false", "no", "off", "never"):
        return False
    if normalized in ("1", "true", "yes", "on", "always"):
        return True
    lower = prompt.casefold()
    return any(hint in lower for hint in _SPEECH_HINTS)


def transcribe_audio_file(
    audio_path: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> str | None:
    if os.path.getsize(audio_path) > MAX_AUDIO_BYTES:
        logger.warning("Аудио слишком большое для транскрипции")
        return None

    url = f"{base_url.rstrip('/')}/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    with open(audio_path, "rb") as audio_file:
        response = requests.post(
            url,
            headers=headers,
            files={"file": (os.path.basename(audio_path), audio_file, "audio/wav")},
            data={"model": model, "language": "ru"},
            timeout=45,
        )
    if response.status_code >= 400:
        logger.warning(
            "Whisper API %s: %s",
            response.status_code,
            (response.text or "")[:200],
        )
        return None

    payload = response.json()
    if isinstance(payload, dict):
        text = payload.get("text")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return None


def transcribe_videos_in_message(
    session: requests.Session,
    vk: Any,
    message_data: dict[str, Any] | None,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> str | None:
    videos = get_video_items(message_data)
    if not videos:
        return None

    transcripts: list[str] = []
    for index, video in enumerate(videos, start=1):
        try:
            with tempfile.TemporaryDirectory() as tmp_dir:
                source_path = download_video_file(session, vk, video, tmp_dir)
                wav_path = os.path.join(tmp_dir, "speech.wav")
                if not extract_audio_wav(source_path, wav_path, MAX_TRANSCRIBE_SECONDS):
                    continue
                text = transcribe_audio_file(
                    wav_path,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                )
                if text:
                    prefix = f"Видео {index}: " if len(videos) > 1 else ""
                    transcripts.append(f"{prefix}{text}")
        except Exception:
            logger.exception("Не удалось расшифровать видео %d", index)

    if not transcripts:
        return None
    logger.info("Расшифровка видео: %d фрагмент(ов)", len(transcripts))
    return "\n".join(transcripts)
