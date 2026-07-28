"""TTS: BotHub/OpenAI-compatible API + локальный edge-tts fallback."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import subprocess
import sys
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


def _edge_tts_async(text: str, voice: str) -> bytes:
    import edge_tts

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

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_run())
    finally:
        loop.close()


def _edge_tts_cli_mp3(text: str, voice: str) -> bytes:
    edge_voice = resolve_edge_voice(voice)
    with tempfile.TemporaryDirectory() as tmp_dir:
        text_path = os.path.join(tmp_dir, "speech.txt")
        mp3_path = os.path.join(tmp_dir, "speech.mp3")
        Path(text_path).write_text(text, encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "edge_tts",
            "-f",
            text_path,
            "-v",
            edge_voice,
            "--write-media",
            mp3_path,
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=90)
        if result.returncode != 0:
            raise RuntimeError(
                f"edge-tts CLI: {(result.stderr or result.stdout or '')[:300]}"
            )
        if not os.path.isfile(mp3_path):
            raise RuntimeError("edge-tts CLI не создал mp3")
        return Path(mp3_path).read_bytes()


def synthesize_edge_speech(text: str, voice: str) -> bytes:
    errors: list[str] = []
    for name, runner in (
        ("edge-tts", _edge_tts_async),
        ("edge-tts-cli", _edge_tts_cli_mp3),
    ):
        try:
            data = runner(text, voice)
            logger.info("TTS %s (%d байт)", name, len(data))
            return data
        except Exception as exc:
            message = f"{name}: {exc}"
            errors.append(message)
            logger.warning("TTS %s не сработал: %s", name, exc)
    raise RuntimeError("; ".join(errors) or "edge-tts недоступен")


def _edge_tts_mp3(text: str, voice: str) -> bytes:
    try:
        import edge_tts  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "edge-tts не установлен. На Bothost: pip install edge-tts в requirements.txt"
        ) from exc
    return synthesize_edge_speech(text, voice)


def log_tts_startup(tts_provider: str) -> None:
    ffmpeg = find_ffmpeg()
    try:
        import edge_tts  # noqa: F401

        edge_ok = "да"
    except ImportError:
        edge_ok = "нет"
    try:
        import omnivoice  # noqa: F401

        omni_ok = "да"
    except ImportError:
        omni_ok = "нет"
    logger.info(
        "TTS: режим=%s ffmpeg=%s edge-tts=%s omnivoice=%s",
        tts_provider,
        ffmpeg or "нет",
        edge_ok,
        omni_ok,
    )


def wav_bytes_to_voice_ogg(wav_data: bytes) -> str:
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            return _wav_to_ogg_ffmpeg(wav_data, ffmpeg)
        except RuntimeError as exc:
            logger.warning("ffmpeg WAV→OGG не удался (%s), пробую PyAV", exc)

    try:
        return _wav_to_ogg_pyav(wav_data)
    except Exception as exc:
        logger.warning("PyAV WAV→OGG не удался (%s)", exc)

    raise RuntimeError(
        "Нет ffmpeg для озвучки. Пересобери бота — в requirements.txt должен быть imageio-ffmpeg."
    )


def _wav_to_ogg_ffmpeg(wav_data: bytes, ffmpeg: str) -> str:
    with tempfile.TemporaryDirectory() as tmp_dir:
        wav_path = os.path.join(tmp_dir, "speech.wav")
        ogg_path = os.path.join(tmp_dir, "voice.ogg")
        Path(wav_path).write_bytes(wav_data)

        command = [
            ffmpeg,
            "-y",
            "-i",
            wav_path,
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
            logger.error("ffmpeg OmniVoice stderr: %s", (result.stderr or "")[-500:])
            raise RuntimeError("ffmpeg не сконвертировал OmniVoice в голосовое")

        return _finalize_ogg_path(ogg_path)


def _wav_to_ogg_pyav(wav_data: bytes) -> str:
    import av

    with tempfile.TemporaryDirectory() as tmp_dir:
        ogg_path = os.path.join(tmp_dir, "voice.ogg")
        input_container = av.open(io.BytesIO(wav_data))
        output_container = av.open(ogg_path, mode="w", format="ogg")
        output_stream = output_container.add_stream("libopus", rate=48000)
        output_stream.layout = "mono"
        output_stream.bit_rate = 32000
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

        seconds = 0.0
        for frame in input_container.decode(audio=0):
            seconds += float(frame.samples) / float(frame.sample_rate or 1)
            if seconds > MAX_VOICE_SEC:
                break
            for resampled in resampler.resample(frame):
                resampled.pts = None
                for packet in output_stream.encode(resampled):
                    output_container.mux(packet)
        for packet in output_stream.encode(None):
            output_container.mux(packet)

        output_container.close()
        input_container.close()
        return _finalize_ogg_path(ogg_path)


def mp3_bytes_to_voice_ogg(mp3_data: bytes) -> str:
    ffmpeg = find_ffmpeg()
    if ffmpeg:
        try:
            return _mp3_to_ogg_ffmpeg(mp3_data, ffmpeg)
        except RuntimeError as exc:
            logger.warning("ffmpeg конвертация не удалась (%s), пробую PyAV", exc)

    try:
        return _mp3_to_ogg_pyav(mp3_data)
    except Exception as exc:
        logger.warning("PyAV конвертация не удалась (%s)", exc)

    raise RuntimeError(
        "Нет ffmpeg для озвучки. Пересобери бота — в requirements.txt должен быть imageio-ffmpeg."
    )


def _mp3_to_ogg_ffmpeg(mp3_data: bytes, ffmpeg: str) -> str:
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

        return _finalize_ogg_path(ogg_path)


def _mp3_to_ogg_pyav(mp3_data: bytes) -> str:
    import av

    with tempfile.TemporaryDirectory() as tmp_dir:
        ogg_path = os.path.join(tmp_dir, "voice.ogg")
        input_container = av.open(io.BytesIO(mp3_data))
        output_container = av.open(ogg_path, mode="w", format="ogg")
        output_stream = output_container.add_stream("libopus", rate=48000)
        output_stream.layout = "mono"
        output_stream.bit_rate = 32000
        resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

        seconds = 0.0
        for frame in input_container.decode(audio=0):
            seconds += float(frame.samples) / float(frame.sample_rate or 1)
            if seconds > MAX_VOICE_SEC:
                break
            for resampled in resampler.resample(frame):
                resampled.pts = None
                for packet in output_stream.encode(resampled):
                    output_container.mux(packet)
        for packet in output_stream.encode(None):
            output_container.mux(packet)

        output_container.close()
        input_container.close()
        return _finalize_ogg_path(ogg_path)


def _finalize_ogg_path(ogg_path: str) -> str:
    if not os.path.isfile(ogg_path):
        raise RuntimeError("не создан ogg-файл")
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
    tts_provider: str = "auto",
    allow_edge_fallback: bool = True,
    omnivoice_model: str = "k2-fsa/OmniVoice",
    omnivoice_instruct: str = "",
    omnivoice_ref_audio: str = "",
    omnivoice_ref_text: str = "",
    omnivoice_language: str = "Russian",
) -> tuple[str, str]:
    speech_text = text_for_speech(text)
    openai_voice = resolve_openai_voice(voice)
    mode = (tts_provider or "auto").strip().lower()

    if mode == "omnivoice":
        try:
            from omnivoice_client import is_omnivoice_ready, synthesize_omnivoice_wav

            if not is_omnivoice_ready(omnivoice_model):
                logger.warning(
                    "OmniVoice не загружена (~3 GB) — озвучка через edge-tts. "
                    "Для OmniVoice: AI_TTS_PROVIDER=omnivoice и дождись загрузки, "
                    "или OMNIVOICE_PRELOAD=1 при старте."
                )
                mp3_data = synthesize_edge_speech(speech_text, voice)
                return mp3_bytes_to_voice_ogg(mp3_data), "edge-tts"

            wav_data = synthesize_omnivoice_wav(
                speech_text,
                model_id=omnivoice_model,
                voice_id=voice,
                instruct=omnivoice_instruct,
                ref_audio=omnivoice_ref_audio,
                ref_text=omnivoice_ref_text,
                language=omnivoice_language,
            )
            return wav_bytes_to_voice_ogg(wav_data), "omnivoice"
        except Exception as exc:
            if not allow_edge_fallback:
                raise
            logger.warning("OmniVoice не сработала (%s), пробую edge-tts", exc)
            mp3_data = synthesize_edge_speech(speech_text, voice)
            return mp3_bytes_to_voice_ogg(mp3_data), "edge-tts"

    mp3_data: bytes
    provider = "edge-tts"

    if mode == "edge":
        mp3_data = synthesize_edge_speech(speech_text, voice)
    elif mode == "bothub":
        mp3_data, provider = synthesize_speech(
            speech_text,
            api_key=api_key,
            base_url=base_url,
            model=model,
            voice=openai_voice,
            tts_base_url=tts_base_url,
        )
    else:
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
            mp3_data = synthesize_edge_speech(speech_text, voice)
            provider = "edge-tts"

    return mp3_bytes_to_voice_ogg(mp3_data), provider
