"""Загрузка картинок и GIF в беседу VK."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests
from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

from ai_client import format_bot_message

logger = logging.getLogger(__name__)

_PHOTO_MIME = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
}


def _photo_attachment(photo: dict[str, Any]) -> str:
    owner_id = photo["owner_id"]
    photo_id = photo["id"]
    access_key = photo.get("access_key")
    if access_key:
        return f"photo{owner_id}_{photo_id}_{access_key}"
    return f"photo{owner_id}_{photo_id}"


def _upload_message_photo(vk: Any, temp_path: str, peer_id: int, *, attempts: int = 3) -> dict[str, Any]:
    """Загрузка картинки в messages через photos.getMessagesUploadServer."""
    ext = Path(temp_path).suffix.lstrip(".").lower() or "png"
    mime = _PHOTO_MIME.get(ext, "image/png")

    last_error = "неизвестная ошибка загрузки"
    session = requests.Session()
    for attempt in range(max(1, attempts)):
        upload_url = vk.photos.getMessagesUploadServer(peer_id=peer_id)["upload_url"]
        with open(temp_path, "rb") as photo_file:
            response = session.post(
                upload_url,
                files={"photo": (f"image.{ext}", photo_file, mime)},
                timeout=90,
            )
        if response.status_code >= 500:
            last_error = f"HTTP {response.status_code} от upload-сервера VK"
            if attempt + 1 < attempts:
                time.sleep(1.5)
            continue
        try:
            payload = response.json()
        except ValueError:
            last_error = f"upload-сервер вернул не JSON: {response.text[:160]!r}"
            continue
        if not payload.get("photo"):
            last_error = f"upload-сервер без photo: {payload!r}"
            continue
        saved = vk.photos.saveMessagesPhoto(**payload)
        return saved[0] if isinstance(saved, list) else saved

    raise RuntimeError(last_error)


def build_reply_params(
    peer_id: int,
    event: Any,
    message: str,
    attachment: str | None,
    *,
    reply_to_cmid: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "message": format_bot_message(message),
        "random_id": get_random_id(),
    }
    if attachment:
        params["attachment"] = attachment

    cmid = reply_to_cmid
    if cmid is None:
        message_data = getattr(event, "message_data", None)
        if isinstance(message_data, dict):
            raw_cmid = message_data.get("conversation_message_id")
            if raw_cmid is not None:
                try:
                    cmid = int(raw_cmid)
                except (TypeError, ValueError):
                    cmid = None

    if cmid is not None:
        params["forward"] = json.dumps(
            {
                "peer_id": peer_id,
                "conversation_message_ids": [cmid],
                "is_reply": 1,
            },
            ensure_ascii=False,
        )
    elif getattr(event, "message_id", None):
        params["reply_to"] = event.message_id
    return params


def send_image_bytes(
    vk_session: Any,
    vk: Any,
    peer_id: int,
    event: Any,
    image_bytes: bytes,
    *,
    caption: str = "",
    suffix: str = ".png",
    reply_to_cmid: int | None = None,
    as_document: bool = False,
    photo_only: bool = False,
) -> None:
    if not image_bytes:
        raise RuntimeError("Пустой файл картинки")

    ext = suffix if suffix.startswith(".") else f".{suffix}"
    fd, temp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "wb") as temp_file:
            temp_file.write(image_bytes)
    except Exception:
        os.unlink(temp_path)
        raise

    try:
        uploader = VkUpload(vk_session)
        attachment: str
        use_photo = not as_document and ext.lower() in (".jpg", ".jpeg", ".png", ".webp")

        if use_photo:
            try:
                photo = _upload_message_photo(
                    vk,
                    temp_path,
                    peer_id,
                    attempts=3 if photo_only else 2,
                )
                attachment = _photo_attachment(photo)
            except Exception as exc:
                if photo_only:
                    raise RuntimeError(f"Не удалось отправить фото: {exc}") from exc
                logger.warning(
                    "photo_messages не удался (%s), пробую doc peer_id=%s",
                    exc,
                    peer_id,
                )
                document = uploader.document_message(
                    temp_path,
                    peer_id=peer_id,
                    title=f"image{ext}",
                )
                doc = document["doc"]
                attachment = f"doc{doc['owner_id']}_{doc['id']}"
        else:
            document = uploader.document_message(
                temp_path,
                peer_id=peer_id,
                title=f"image{ext}",
            )
            doc = document["doc"]
            attachment = f"doc{doc['owner_id']}_{doc['id']}"

        vk.messages.send(
            **build_reply_params(
                peer_id,
                event,
                caption,
                attachment,
                reply_to_cmid=reply_to_cmid,
            )
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def send_gif_url(
    vk_session: Any,
    vk: Any,
    peer_id: int,
    event: Any,
    gif_url: str,
    *,
    caption: str = "",
    reply_to_cmid: int | None = None,
) -> None:
    response = requests.get(gif_url, timeout=45)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as temp_file:
        temp_file.write(response.content)
        temp_path = temp_file.name

    try:
        uploader = VkUpload(vk_session)
        document = uploader.document_message(temp_path, peer_id=peer_id, title="gif.gif")
        doc = document["doc"]
        attachment = f"doc{doc['owner_id']}_{doc['id']}"
        vk.messages.send(
            **build_reply_params(
                peer_id,
                event,
                caption,
                attachment,
                reply_to_cmid=reply_to_cmid,
            )
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
