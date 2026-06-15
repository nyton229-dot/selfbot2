"""Загрузка картинок и GIF в беседу VK."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

import requests
from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

from ai_client import format_bot_message

logger = logging.getLogger(__name__)


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
    suffix: str = ".jpg",
    reply_to_cmid: int | None = None,
    as_document: bool = False,
) -> None:
    if not image_bytes:
        raise RuntimeError("Пустой файл картинки")

    ext = suffix if suffix.startswith(".") else f".{suffix}"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as temp_file:
        temp_file.write(image_bytes)
        temp_path = temp_file.name

    try:
        uploader = VkUpload(vk_session)
        attachment: str
        use_photo = not as_document and ext.lower() in (".jpg", ".jpeg", ".png", ".webp")

        if use_photo:
            try:
                photos = uploader.photo_messages(temp_path, peer_id=peer_id)
                photo = photos[0] if isinstance(photos, list) else photos
                attachment = f"photo{photo['owner_id']}_{photo['id']}"
            except Exception:
                logger.exception("photo_messages не удался, пробую doc peer_id=%s", peer_id)
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
