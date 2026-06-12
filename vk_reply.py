"""«артем ответь ему» — ответ на сообщение другого человека через VK reply."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_VK_USER_MENTION_RE = re.compile(r"\[id(\d+)\|([^\]]+)\]", re.IGNORECASE)

_REPLY_CMD_RE = re.compile(
    r"^(?:ответь|reply)\s+"
    r"(?:"
    r"(?:ему|ей|им|его|её|него|неё)"
    r"|(?:на\s+(?:это|сообщение|него|неё|его|её))"
    r")"
    r"(?:\s+(?P<extra>.+))?$",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class ReplyToUserCommand:
    extra: str = ""


@dataclass(frozen=True)
class ReplyToUserContext:
    target_user_id: int
    target_name: str
    target_mention: str
    replied_text: str
    reply_cmid: int | None
    extra_instruction: str = ""


def parse_reply_to_user_command(prompt: str) -> ReplyToUserCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _REPLY_CMD_RE.match(stripped)
    if not match:
        return None
    extra = (match.group("extra") or "").strip()
    return ReplyToUserCommand(extra=extra)


def get_reply_message(message_data: dict[str, Any] | None) -> dict[str, Any] | None:
    if not message_data:
        return None
    reply = message_data.get("reply_message")
    return reply if isinstance(reply, dict) else None


def reply_message_conversation_id(reply: dict[str, Any]) -> int | None:
    cmid = reply.get("conversation_message_id")
    if cmid is None:
        return None
    try:
        return int(cmid)
    except (TypeError, ValueError):
        return None


def reply_message_text(reply: dict[str, Any]) -> str:
    text = (reply.get("text") or "").strip()
    if text:
        return text

    attachments = reply.get("attachments")
    if not isinstance(attachments, list):
        return "[сообщение без текста]"

    types = {
        item.get("type")
        for item in attachments
        if isinstance(item, dict) and item.get("type")
    }
    if "photo" in types:
        return "[фото]"
    if "video" in types or "video_message" in types:
        return "[видео]"
    if "audio" in types or "audio_message" in types:
        return "[аудио]"
    if "doc" in types:
        return "[файл]"
    if "sticker" in types:
        return "[стикер]"
    if types:
        return "[вложение]"
    return "[сообщение без текста]"


def format_user_mention(user_id: int, display_name: str) -> str:
    label = display_name.strip() or f"id{user_id}"
    return f"[id{user_id}|{label}]"


def mention_label(full_name: str, user_id: int) -> str:
    first = full_name.strip().split()[0] if full_name.strip() else f"id{user_id}"
    return first


def strip_vk_user_mentions(text: str) -> str:
    """Убирает [id123|Имя] целиком."""
    cleaned = _VK_USER_MENTION_RE.sub("", text)
    cleaned = re.sub(r" +", " ", cleaned)
    cleaned = re.sub(r"\s+,", ",", cleaned)
    cleaned = re.sub(r"^,\s*", "", cleaned.strip())
    return cleaned.strip()


def _names_for_reply_target(context: ReplyToUserContext) -> tuple[str, ...]:
    names: list[str] = []
    full = context.target_name.strip()
    if full:
        names.append(full)
        first = full.split(maxsplit=1)[0]
        if first:
            names.append(first)
    label = context.target_mention.split("|", 1)[-1].rstrip("]").strip()
    if label:
        names.append(label)
    unique: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        if len(name) >= 2 and key not in seen:
            seen.add(key)
            unique.append(name)
    return tuple(unique)


def strip_leading_target_name(text: str, context: ReplyToUserContext) -> str:
    """Убирает имя адресата в начале: «Meow, ты чё» → «ты чё»."""
    result = text.strip()
    for name in sorted(_names_for_reply_target(context), key=len, reverse=True):
        for pattern in (
            re.compile(rf"^{re.escape(name)}\s*[,!:—\-–]\s*", re.IGNORECASE),
            re.compile(rf"^{re.escape(name)}\s+", re.IGNORECASE),
        ):
            updated = pattern.sub("", result, count=1)
            if updated != result:
                result = updated.strip()
                break
    return result


def clean_reply_to_user_text(text: str, context: ReplyToUserContext) -> str:
    """Reply-to-user: без [id|...] и без имени в начале."""
    cleaned = strip_vk_user_mentions(text)
    cleaned = strip_leading_target_name(cleaned, context)
    cleaned = re.sub(r" +", " ", cleaned)
    cleaned = re.sub(r"^,\s*", "", cleaned.strip())
    return cleaned.strip()
