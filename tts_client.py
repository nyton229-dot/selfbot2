"""TTS: BotHub/OpenAI-compatible API + локальный edge-tts fallback."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

import requests

from ffmpeg_util import find_ffmpeg
from tts_voices import resolve_edge_voice, resolve_openai_voice

logger = logging.getLogger(__name__)

MAX_TTS_CHARS = 900
MAX_VOICE_SEC = 60
MAX_VOICE_BYTES = 20 * 1024 * 1024

_BOTHUB_TTS_BASES = (
    "https://openai.bothub.chat/v1",
    "https://bothub.chat/api/v2/openai/v1",
)


def resolve_tts_bases(primary_base_url: str, explicit_tts_base_url: str = "") -> tuple[str, ...]:
    bases: list[str] = []
    for candidate in (explicit_tts_base_url, primary_base_url, *_BOTHUB_TTS_BASES):
        value = candidate.strip().rstrip("/")
        if value and value not in bases:
            bases.append(value)
    return tuple(bases)


def text_for_speech(text: str) -> str:
    """Для озвучки — чуть чище, чем чатовые косяки (иначе TTS кашляет)."""
    cleaned = text.strip()[:MAX_TTS_CHARS]
    if not cleaned:
        return cleaned
    replacements = (
        (r"\bшто\b", "что"),
        (r"\bщас\b", "сейчас"),
        (r"\bпатамушта\b", "потому что"),
        (r"\bканешно\b", "конечно"),
        (r"\bничё\b", "ничего"),
        (r"\bваще\b", "вообще"),
        (r"\bишо\b", "ещё"),
        (r"\bпачему\b", "почему"),
        (r"\bкагда\b", "когда"),
        (r"\bето\b", "это"),
        (r"\bдла\b", "для"),
        (r"\bатвечаю\b", "отвечаю"),
        (r"\bжывой\b", "живой"),
        (r"\bдешовая\b", "дешевая"),
        (r"\bчелавека\b", "человека"),
        (r"\bпазорище\b", "позорище"),
    )
    result = cleaned
    for pattern, replacement in replacements:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    result = re.sub(r"\.+", " ", result)
    result = re.sub(r" +", " ", result)
    return result.strip()


def _tts_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/audio/speech"


def _synthesize_once(
    text: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    voice: str,
) -> bytes:
    response = requests.post(
        _tts_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "audio/mpeg",
        },
        json={
            "model": model,
            "input": text,
            "voice": voice,
            "response_format": "mp3",
        },
        timeout=90,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"TTS API {response.status_code} @ {base_url}: {(response.text or '')[:300]}"
        )
    content = response.content
    if not content:
        raise RuntimeError(f"TTS API вернул пустой файл @ {base_url}")
    return content


def synthesize_speech(
    text: str,
    *,
    api_key: str,
    base_url: str,
    model: str = "tts-1-1106",
    voice: str = "onyx",
    tts_base_url: str = "",
) -> tuple[bytes, str]:
    payload = text_for_speech(text)
    if not payload:
        raise ValueError("Пустой текст для озвучки")

    models = tuple(dict.fromkeys((model, "tts-1-1106", "tts-1-hd-1106", "tts-1", "tts-1-hd")))
    errors: list[str] = []
    for base in resolve_tts_bases(base_url, tts_base_url):
        for tts_model in models:
            try:
                content = _synthesize_once(
                    payload,
                    api_key=api_key,
                    base_url=base,
                    model=tts_model,
                    voice=voice,
                )
                logger.info("TTS BotHub: base=%s model=%s (%d байт)", base, tts_model, len(content))
                return content, f"bothub:{tts_model}"
            except RuntimeError as exc:
                message = str(exc)
                errors.append(message)
                if "401" in message or "403" in message:
                    break
    raise RuntimeError("; ".join(errors[-3:]) or "TTS BotHub недоступен")


def _edge_tts_mp3(text: str, voice: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError(
            "BotHub TTS недоступен. Установи edge-tts: pip install edge-tts"
        ) from exc

    edge_voice = resolve_edge_voice(voice)

    async def _run() -> bytes:
        communicate = edge_tts.Communicate(text, edge_voice)
        chunks: list[bytes] = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                chunks.append(chunk["data"])
        data = b"".join(chunks)
        if not data:
            raise RuntimeError("edge-tts вернул пустой аудиофайл")
        return data

    return asyncio.run(_run())


def mp3_bytes_to_voice_ogg(mp3_data: bytes) -> str:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "Нужен ffmpeg для озвучки. Установи: winget install Gyan.FFmpeg"
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        mp3_path = os.path.join(tmp_dir, "speech.mp3")
        ogg_path = os.path.join(tmp_dir, "voice.ogg")
        Path(mp3_path).write_bytes(mp3_data)

        command = [
            ffmpeg,
            "-y",
            "-i",
            mp3_path,
            "-t",
            str(MAX_VOICE_SEC),
            "-vn",
            "-ac",
            "1",
            "-c:a",
            "libopus",
            "-b:a",
            "32k",
            "-application",
            "voip",
            "-ar",
            "48000",
            ogg_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            logger.error("ffmpeg TTS stderr: %s", (result.stderr or "")[-500:])
            raise RuntimeError("ffmpeg не сконвертировал озвучку в голосовое")

        if not os.path.isfile(ogg_path):
            raise RuntimeError("ffmpeg не создал ogg-файл")
        if os.path.getsize(ogg_path) > MAX_VOICE_BYTES:
            raise RuntimeError("Голосовое слишком длинное — сократи запрос")

        out_path = os.path.join(tempfile.gettempdir(), f"lpbot_tts_{os.getpid()}.ogg")
        Path(out_path).write_bytes(Path(ogg_path).read_bytes())
        return out_path


def text_to_voice_file(
    text: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    voice: str,
    tts_base_url: str = "",
    allow_edge_fallback: bool = True,
) -> tuple[str, str]:
    speech_text = text_for_speech(text)
    openai_voice = resolve_openai_voice(voice)
    provider = "bothub"
    try:
        mp3_data, provider = synthesize_speech(
            speech_text,
            api_key=api_key,
            base_url=base_url,
            model=model,
            voice=openai_voice,
            tts_base_url=tts_base_url,
        )
    except RuntimeError as exc:
        if not allow_edge_fallback:
            raise
        logger.warning("BotHub TTS не сработал (%s), пробую edge-tts", exc)
        mp3_data = _edge_tts_mp3(speech_text, voice)
        provider = "edge-tts"
        logger.info("TTS edge-tts (%d байт)", len(mp3_data))

    return mp3_bytes_to_voice_ogg(mp3_data), provider
