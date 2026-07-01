"""Команды /промпт и «артем промпт …» — смена системных промптов."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPT_COMMANDS = ("/промпт", "/промт", "/prompt")
MAX_PROMPT_CHARS = 12000
_PREVIEW_CHARS = 180

_ARTEM_PROMPT_RE = re.compile(r"^промп?т(?:\s+(?P<rest>.+))?$", re.IGNORECASE | re.DOTALL)

_store: PromptStore | None = None


@dataclass(frozen=True)
class PromptCommand:
    action: str
    target: str = "text"
    body: str = ""


def is_prompt_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in PROMPT_COMMANDS:
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def is_artem_prompt_command(prompt: str) -> bool:
    return _ARTEM_PROMPT_RE.match(prompt.strip()) is not None


def _strip_command(text: str) -> str:
    stripped = text.strip()
    lower = stripped.casefold()
    for prefix in sorted(PROMPT_COMMANDS, key=len, reverse=True):
        cmd = prefix.casefold()
        if lower == cmd:
            return ""
        if lower.startswith(cmd + " "):
            return stripped[len(prefix) :].strip()
    return stripped


def _parse_rest(rest: str) -> PromptCommand:
    stripped = rest.strip()
    if not stripped:
        return PromptCommand("show")

    lower = stripped.casefold()
    if lower in ("список", "list", "показ", "show", "статус", "status"):
        return PromptCommand("show")

    if lower in ("сброс", "reset", "дефолт", "default"):
        return PromptCommand("reset", "text")

    if lower in ("сброс фото", "reset photo", "reset vision"):
        return PromptCommand("reset", "vision")

    if lower in ("сброс все", "reset all"):
        return PromptCommand("reset", "all")

    if lower.startswith("фото ") or lower.startswith("photo "):
        body = stripped.split(" ", 1)[1].strip() if " " in stripped else ""
        return PromptCommand("set", "vision", body)

    if lower.startswith("текст ") or lower.startswith("text "):
        body = stripped.split(" ", 1)[1].strip() if " " in stripped else ""
        return PromptCommand("set", "text", body)

    if lower in ("фото", "photo", "vision"):
        return PromptCommand("show", "vision")

    if lower in ("текст", "text"):
        return PromptCommand("show", "text")

    return PromptCommand("set", "text", stripped)


def parse_prompt_command(text: str) -> PromptCommand | None:
    if is_prompt_command(text):
        return _parse_rest(_strip_command(text))
    match = _ARTEM_PROMPT_RE.match(text.strip())
    if match is None:
        return None
    return _parse_rest(match.group("rest") or "")


def init_prompt_store(
    path: Path,
    *,
    default_text: str,
    default_vision: str,
) -> PromptStore:
    global _store
    _store = PromptStore(path, default_text=default_text, default_vision=default_vision)
    return _store


def get_prompt_store() -> PromptStore:
    if _store is None:
        raise RuntimeError("PromptStore не инициализирован")
    return _store


class PromptStore:
    def __init__(self, path: Path, *, default_text: str, default_vision: str) -> None:
        self._path = path
        self._default_text = default_text.strip()
        self._default_vision = default_vision.strip()
        self._text_prompt: str | None = None
        self._vision_prompt: str | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать %s", self._path)
            return
        if not isinstance(raw, dict):
            return
        text = raw.get("text_prompt")
        vision = raw.get("vision_prompt")
        if isinstance(text, str) and text.strip():
            self._text_prompt = text.strip()
        if isinstance(vision, str) and vision.strip():
            self._vision_prompt = vision.strip()

    def _save(self) -> None:
        payload: dict[str, str] = {}
        if self._text_prompt:
            payload["text_prompt"] = self._text_prompt
        if self._vision_prompt:
            payload["vision_prompt"] = self._vision_prompt
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def text_prompt(self) -> str | None:
        return self._text_prompt

    @property
    def vision_prompt(self) -> str | None:
        return self._vision_prompt

    def set_text_prompt(self, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Пустой промпт")
        if len(cleaned) > MAX_PROMPT_CHARS:
            raise ValueError(f"Промпт длиннее {MAX_PROMPT_CHARS} символов")
        self._text_prompt = cleaned
        self._save()
        logger.info("Промпт текста обновлён (%d симв.)", len(cleaned))

    def set_vision_prompt(self, value: str) -> None:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Пустой промпт")
        if len(cleaned) > MAX_PROMPT_CHARS:
            raise ValueError(f"Промпт длиннее {MAX_PROMPT_CHARS} символов")
        self._vision_prompt = cleaned
        self._save()
        logger.info("Промпт фото обновлён (%d симв.)", len(cleaned))

    def reset_text(self) -> None:
        self._text_prompt = None
        self._save()
        logger.info("Промпт текста сброшен на дефолт из config")

    def reset_vision(self) -> None:
        self._vision_prompt = None
        self._save()
        logger.info("Промпт фото сброшен на дефолт из config")

    def reset_all(self) -> None:
        self._text_prompt = None
        self._vision_prompt = None
        self._save()
        logger.info("Промпты сброшены на дефолт из config")


def _preview(text: str, *, custom: bool) -> str:
    label = "свой" if custom else "дефолт"
    snippet = text.replace("\n", " ").strip()
    if len(snippet) > _PREVIEW_CHARS:
        snippet = snippet[: _PREVIEW_CHARS - 1] + "…"
    return f"{label}: {snippet}"


def format_prompt_status(store: PromptStore, *, default_text: str, default_vision: str) -> str:
    text = store.text_prompt or default_text
    vision = store.vision_prompt or default_vision
    lines = [
        "Промпты бота:",
        f"• Текст — {_preview(text, custom=store.text_prompt is not None)}",
        f"• Фото — {_preview(vision, custom=store.vision_prompt is not None)}",
        "",
        "Задать:",
        "/промпт ты злой тролль…",
        "/промпт текст … — только для сообщений",
        "/промпт фото … — только для картинок",
        "Reply на сообщение + «/промпт» — взять его текст",
        "Reply + «/промпт фото» — промпт для фото",
        "",
        "Сброс:",
        "/промпт сброс — текст",
        "/промпт сброс фото",
        "/промпт сброс все",
        "",
        "То же через «артем промпт …»",
    ]
    return "\n".join(lines)


def apply_prompt_command(
    store: PromptStore,
    command: PromptCommand,
    *,
    default_text: str,
    default_vision: str,
    reply_text: str | None = None,
) -> str:
    if command.action == "show":
        if command.target == "text":
            text = store.text_prompt or default_text
            return _preview(text, custom=store.text_prompt is not None)
        if command.target == "vision":
            vision = store.vision_prompt or default_vision
            return _preview(vision, custom=store.vision_prompt is not None)
        return format_prompt_status(store, default_text=default_text, default_vision=default_vision)

    if command.action == "reset":
        if command.target == "all":
            store.reset_all()
            return "Сбросил оба промпта — снова дефолт из config."
        if command.target == "vision":
            store.reset_vision()
            return "Промпт для фото сброшен на дефолт."
        store.reset_text()
        return "Промпт для текста сброшен на дефолт."

    body = (command.body or "").strip()
    if not body and reply_text:
        body = reply_text.strip()
    if not body:
        raise ValueError(
            "Напиши промпт после команды или ответь (reply) на сообщение с текстом."
        )

    if command.target == "vision":
        store.set_vision_prompt(body)
        return f"Промпт для фото сохранён ({len(body)} симв.)."

    store.set_text_prompt(body)
    return f"Промпт для текста сохранён ({len(body)} симв.)."
