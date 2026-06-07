"""Команда /вгс — видео → голосовое сообщение без ИИ."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

from ffmpeg_util import find_ffmpeg as _find_ffmpeg
from vk_media import download_video_file, find_video_object

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


def _extract_uploaded_doc(upload_result: Any) -> dict[str, Any]:
    payload = upload_result
    if isinstance(payload, list):
        if not payload:
            raise RuntimeError("VK вернул пустой ответ после загрузки голосового")
        payload = payload[0]
    if not isinstance(payload, dict):
        raise RuntimeError("VK вернул неожиданный ответ после загрузки голосового")

    for key in ("audio_message", "doc", "graffiti"):
        doc = payload.get(key)
        if isinstance(doc, dict) and doc.get("owner_id") is not None and doc.get("id") is not None:
            return doc

    if payload.get("owner_id") is not None and payload.get("id") is not None:
        return payload

    logger.error("Неожиданный ответ docs.save: %s", list(payload.keys()))
    raise RuntimeError("VK не вернул документ после загрузки голосового")


def _build_doc_attachment(doc: dict[str, Any]) -> str:
    owner_id = doc["owner_id"]
    doc_id = doc["id"]
    access_key = doc.get("access_key") or ""
    suffix = f"{owner_id}_{doc_id}"
    if access_key:
        suffix += f"_{access_key}"
    return f"doc{suffix}"


def _reply_params(peer_id: int, event: Any, attachment: str) -> dict[str, Any]:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "message": "",
        "random_id": get_random_id(),
        "attachment": attachment,
    }
    message_data = getattr(event, "message_data", None)
    if isinstance(message_data, dict):
        raw_cmid = message_data.get("conversation_message_id")
        if raw_cmid is not None:
            params["forward"] = json.dumps(
                {
                    "peer_id": peer_id,
                    "conversation_message_ids": [int(raw_cmid)],
                    "is_reply": 1,
                },
                ensure_ascii=False,
            )
            return params
    if getattr(event, "message_id", None):
        params["reply_to"] = event.message_id
    return params


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

        uploader = VkUpload(vk_session)
        upload_result = uploader.audio_message(audio_path, peer_id=peer_id)

    doc = _extract_uploaded_doc(upload_result)
    attachment = _build_doc_attachment(doc)
    vk.messages.send(**_reply_params(peer_id, event, attachment))
