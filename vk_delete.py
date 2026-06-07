"""Фейк-команда /удалить — пран «удаление Артема» тремя сообщениями."""

from __future__ import annotations

DELETE_COMMAND = "/удалить"

FAKE_DELETE_MESSAGES: tuple[str, ...] = (
    "Удаление профиля Артем... начато.",
    "Очистка файлов, фото и переписки...",
    "Артем удален навсегда. Файлы удалены. До свидания.",
)


def is_delete_command(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False

    lower = normalized.casefold()
    cmd = DELETE_COMMAND.casefold()
    if lower == cmd or lower.startswith(f"{cmd} "):
        return True

    if not lower.startswith("артем"):
        return False

    rest = normalized[len("артем") :].lstrip(" ,:-—").strip()
    rest_lower = rest.casefold()
    return rest_lower == cmd or rest_lower.startswith(f"{cmd} ")
