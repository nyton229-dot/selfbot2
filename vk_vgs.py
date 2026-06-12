"""Команда /вгс — видео → голосовое сообщение без ИИ."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from ffmpeg_util import find_ffmpeg as _find_ffmpeg
from vk_media import download_video_file, find_video_object
from vk_voice import build_send_params, upload_audio_message

logger = logging.getLogger(__name__)

VGS_COMMAND = "/вгс"
VGS_COMMAND_ALT = "/vgs"
MAX_DURATION_SEC = 60
MAX_OUTPUT_BYTES = 20 * 1024 * 1024


def is_vgs_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in (VGS_COMMAND, VGS_COMMAND_ALT):
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def _extract_audio(input_path: str, output_path: str) -> None:
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError(
            "Нужен ffmpeg. Установи: winget install Gyan.FFmpeg — и перезапусти терминал."
        )

    command = [
        ffmpeg,
        "-y",
        "-i",
        input_path,
        "-t",
        str(MAX_DURATION_SEC),
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
        output_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.error("ffmpeg stderr: %s", (result.stderr or "")[-500:])
        raise RuntimeError("ffmpeg не смог вытащить звук из видео")

    if not os.path.isfile(output_path):
        raise RuntimeError("ffmpeg не создал аудиофайл")

    if os.path.getsize(output_path) > MAX_OUTPUT_BYTES:
        raise LookupError("Голосовое получилось слишком большим — пришли видео покороче")


def process_vgs_command(
    vk_session: Any,
    vk: Any,
    peer_id: int,
    event: Any,
    message_data: dict[str, Any] | None,
) -> None:
    video = find_video_object(message_data)
    if video is None:
        raise LookupError(
            "Нет видео. Ответь командой /вгс на сообщение с видео или приложи видео."
        )

    with tempfile.TemporaryDirectory() as tmp_dir:
        source_path = download_video_file(vk_session.http, vk, video, tmp_dir)
        audio_path = os.path.join(tmp_dir, "voice.ogg")
        _extract_audio(source_path, audio_path)

        attachment = upload_audio_message(vk_session, peer_id, audio_path)

    vk.messages.send(**build_send_params(peer_id, event, attachment=attachment))
