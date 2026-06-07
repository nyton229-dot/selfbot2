"""Команда /ава — аватар пользователя VK без ИИ."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import requests
from vk_api.exceptions import ApiError
from vk_api.upload import VkUpload
from vk_api.utils import get_random_id

logger = logging.getLogger(__name__)

AVA_COMMAND = "/ава"
AVA_COMMAND_ALT = "/ava"
VK_PROFILE_RE = re.compile(
    r"(?:https?://)?(?:m\.)?(?:vk\.com|vk\.ru)/(?:id(?P<id>\d+)|(?P<screen>[A-Za-z0-9_.]+))",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"^\[(?P<label>[^\]|]+)\|(?P<link>[^\]]+)\]")


@dataclass(frozen=True)
class AvatarPayload:
    caption: str
    is_closed: bool
    attachment: str | None = None
    image_url: str | None = None


def is_ava_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in (AVA_COMMAND, AVA_COMMAND_ALT):
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def _strip_command(text: str) -> str:
    stripped = text.strip()
    lower = stripped.casefold()
    for prefix in (AVA_COMMAND, AVA_COMMAND_ALT):
        if lower == prefix.casefold():
            return ""
        if lower.startswith(prefix.casefold() + " "):
            return stripped[len(prefix) :].strip()
    return stripped


def parse_profile_reference(text: str) -> str | None:
    rest = _strip_command(text)
    if not rest:
        return None

    mention = MENTION_RE.match(rest)
    if mention:
        rest = mention.group("link").strip()

    match = VK_PROFILE_RE.search(rest)
    if match:
        if match.group("id"):
            return f"id{match.group('id')}"
        screen = match.group("screen")
        if screen.casefold() not in ("wall", "photo", "video", "clips", "feed", "im"):
            return screen

    token = rest.split()[0].strip().strip("<>")
    if token.isdigit():
        return f"id{token}"
    if token.startswith("@"):
        token = token[1:]
    if re.fullmatch(r"[A-Za-z0-9_.]{3,32}", token):
        return token

    return None


def reply_author_id(event: Any) -> int | None:
    message_data = getattr(event, "message_data", None)
    if not isinstance(message_data, dict):
        return None

    reply = message_data.get("reply_message")
    if isinstance(reply, dict):
        from_id = reply.get("from_id")
        if from_id is not None:
            return int(from_id)

    return None


def resolve_user_id(vk: Any, reference: str | None, event: Any) -> int | None:
    if reference:
        ref = reference.strip()
        if ref.lower().startswith("id") and ref[2:].isdigit():
            return int(ref[2:])
        try:
            resolved = vk.utils.resolveScreenName(screen_name=ref.lstrip("@"))
            if resolved and resolved.get("type") == "user":
                return int(resolved["object_id"])
        except ApiError:
            logger.exception("resolveScreenName не удался для %r", ref)
            return None
        return None

    replied = reply_author_id(event)
    if replied is not None:
        return replied

    user_id = int(getattr(event, "user_id", 0))
    return user_id if user_id > 0 else None


def _is_placeholder_url(url: str) -> bool:
    lowered = url.casefold()
    return "camera" in lowered or "deactivated" in lowered


def _largest_photo_url(photo: dict[str, Any]) -> str | None:
    sizes = photo.get("sizes") or []
    if not sizes:
        return photo.get("src") or photo.get("url")

    best = max(sizes, key=lambda item: item.get("width", 0) * item.get("height", 0))
    return best.get("url")


def _photo_from_profile_album(vk: Any, user_id: int) -> AvatarPayload | None:
    try:
        response = vk.photos.get(
            owner_id=user_id,
            album_id="profile",
            count=1,
            photo_sizes=True,
            rev=1,
        )
    except ApiError:
        return None

    items = response.get("items") if isinstance(response, dict) else response
    if not items:
        return None

    photo = items[0]
    url = _largest_photo_url(photo)
    if not url or _is_placeholder_url(url):
        return None

    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    return AvatarPayload(caption="", is_closed=False, attachment=attachment, image_url=url)


def _photo_from_photo_id(vk: Any, photo_id: str) -> AvatarPayload | None:
    try:
        items = vk.photos.getById(photos=photo_id, photo_sizes=True)
    except ApiError:
        logger.exception("photos.getById не удался для %s", photo_id)
        return None

    if not items:
        return None

    photo = items[0]
    url = _largest_photo_url(photo)
    if not url or _is_placeholder_url(url):
        return None

    attachment = f"photo{photo['owner_id']}_{photo['id']}"
    return AvatarPayload(caption="", is_closed=False, attachment=attachment, image_url=url)


def fetch_user_avatar(vk: Any, user_id: int) -> AvatarPayload:
    """Максимальный размер: нативное фото VK или URL для загрузки файлом."""
    users = vk.users.get(
        user_ids=user_id,
        fields=(
            "photo_id,photo_max,photo_400,photo_max_orig,photo_200,"
            "first_name,last_name,is_closed,can_access_closed"
        ),
    )
    if not users:
        raise LookupError(f"Пользователь {user_id} не найден")

    user = users[0]
    name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or str(user_id)
    is_closed = bool(user.get("is_closed"))
    caption = f"Ава {name}"
    if is_closed:
        caption += " (закрытый профиль — что смог достать)"

    photo_id = user.get("photo_id")
    if photo_id:
        payload = _photo_from_photo_id(vk, str(photo_id))
        if payload:
            return AvatarPayload(
                caption=caption,
                is_closed=is_closed,
                attachment=payload.attachment,
                image_url=payload.image_url,
            )

    if not is_closed:
        payload = _photo_from_profile_album(vk, user_id)
        if payload:
            return AvatarPayload(
                caption=caption,
                is_closed=is_closed,
                attachment=payload.attachment,
                image_url=payload.image_url,
            )

    for key in ("photo_max", "photo_400", "photo_max_orig", "photo_200"):
        url = user.get(key)
        if url and not _is_placeholder_url(url):
            return AvatarPayload(caption=caption, is_closed=is_closed, image_url=url)

    if is_closed:
        raise LookupError(f"У {name} закрытый профиль, аватарку не достал")

    raise LookupError(f"У {name} нет фото профиля")


def _reply_params(peer_id: int, event: Any, message: str, attachment: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "peer_id": peer_id,
        "message": message,
        "random_id": get_random_id(),
    }
    if attachment:
        params["attachment"] = attachment

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


def send_avatar_reply(
    vk_session: Any,
    vk: Any,
    peer_id: int,
    event: Any,
    payload: AvatarPayload,
) -> None:
    if payload.attachment:
        vk.messages.send(**_reply_params(peer_id, event, payload.caption, payload.attachment))
        return

    if not payload.image_url:
        raise LookupError("Нет ссылки на аватар")

    response = requests.get(payload.image_url, timeout=45)
    response.raise_for_status()

    suffix = ".jpg"
    parsed = urlparse(payload.image_url)
    if parsed.path.lower().endswith(".png"):
        suffix = ".png"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_file.write(response.content)
        temp_path = temp_file.name

    try:
        uploader = VkUpload(vk_session)
        document = uploader.document_message(
            temp_path,
            peer_id=peer_id,
            title=f"ava{suffix}",
        )
        doc = document["doc"]
        attachment = f"doc{doc['owner_id']}_{doc['id']}"
        vk.messages.send(**_reply_params(peer_id, event, payload.caption, attachment))
    except ApiError:
        logger.exception("Не удалось загрузить файл, отправляю ссылку peer_id=%s", peer_id)
        vk.messages.send(
            **_reply_params(
                peer_id,
                event,
                f"{payload.caption}\n{payload.image_url}",
                None,
            )
        )
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
