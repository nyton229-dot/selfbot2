"""Загрузка и отправка голосовых сообщений VK."""

from __future__ import annotations

import json
import logging
from typing import Any

from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

logger = logging.getLogger(__name__)


def extract_uploaded_doc(upload_result: Any) -> dict[str, Any]:
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


def build_doc_attachment(doc: dict[str, Any]) -> str:
    owner_id = doc["owner_id"]
    doc_id = doc["id"]
    access_key = doc.get("access_key") or ""
    suffix = f"{owner_id}_{doc_id}"
    if access_key:
        suffix += f"_{access_key}"
    return f"doc{suffix}"


def upload_audio_message(vk_session: Any, peer_id: int, audio_path: str) -> str:
    uploader = VkUpload(vk_session)
    upload_result = uploader.audio_message(audio_path, peer_id=peer_id)
    doc = extract_uploaded_doc(upload_result)
    return build_doc_attachment(doc)


def build_send_params(
    peer_id: int,
    event: Any,
    *,
    message: str = "",
    attachment: str | None = None,
    reply_to_cmid: int | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "message": message,
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


def send_voice_message(
    vk: Any,
    vk_session: Any,
    peer_id: int,
    event: Any,
    audio_path: str,
    *,
    reply_to_cmid: int | None = None,
) -> None:
    attachment = upload_audio_message(vk_session, peer_id, audio_path)
    params = build_send_params(
        peer_id,
        event,
        attachment=attachment,
        reply_to_cmid=reply_to_cmid,
    )
    vk.messages.send(**params)
