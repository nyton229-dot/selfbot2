"""Команда /ии — смена моделей текста и фото/видео."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

II_COMMANDS = ("/ии", "/model", "/модель")

TEXT_MODELS: tuple[tuple[str, str], ...] = (
    ("deepseek-chat", "DeepSeek Chat"),
    ("deepseek-v3.2", "DeepSeek V3.2"),
    ("deepseek-v4-flash", "DeepSeek V4 Flash"),
    ("deepseek-r1", "DeepSeek R1"),
    ("gpt-4o-mini", "GPT-4o Mini"),
    ("gpt-4o", "GPT-4o"),
    ("gpt-4.1-mini", "GPT-4.1 Mini"),
    ("claude-sonnet-4.6", "Claude Sonnet 4.6"),
    ("claude-3.5-haiku", "Claude Haiku 3.5"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
)

VISION_MODELS: tuple[tuple[str, str], ...] = (
    ("claude-sonnet-4.6", "Claude Sonnet 4.6"),
    ("claude-3.5-haiku", "Claude Haiku 3.5"),
    ("gpt-4o", "GPT-4o"),
    ("gpt-4o-mini", "GPT-4o Mini"),
    ("gemini-2.5-flash", "Gemini 2.5 Flash"),
)

_store: AiModelStore | None = None


@dataclass(frozen=True)
class ModelCommand:
    action: str
    model_id: str | None = None


def is_ii_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in II_COMMANDS:
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def _strip_command(text: str) -> str:
    stripped = text.strip()
    lower = stripped.casefold()
    for prefix in II_COMMANDS:
        cmd = prefix.casefold()
        if lower == cmd:
            return ""
        if lower.startswith(cmd + " "):
            return stripped[len(prefix) :].strip()
    return stripped


def _resolve_model_id(raw: str, allowed: tuple[tuple[str, str], ...]) -> str | None:
    token = raw.strip()
    if not token:
        return None
    lower = token.casefold()
    for model_id, _label in allowed:
        if lower == model_id.casefold():
            return model_id
    return None


def parse_ii_command(text: str) -> ModelCommand | None:
    if not is_ii_command(text):
        return None

    rest = _strip_command(text).strip()
    if not rest:
        return ModelCommand("show")

    lower = rest.casefold()
    if lower in ("список", "list", "models"):
        return ModelCommand("list")

    parts = rest.split(maxsplit=1)
    head = parts[0].casefold()
    tail = parts[1].strip() if len(parts) > 1 else ""

    if head in ("текст", "text", "txt") and tail:
        model_id = _resolve_model_id(tail, TEXT_MODELS)
        if model_id:
            return ModelCommand("set_text", model_id)
        return ModelCommand("invalid", tail)

    if head in ("фото", "vision", "media", "медиа", "видео") and tail:
        model_id = _resolve_model_id(tail, VISION_MODELS)
        if model_id:
            return ModelCommand("set_vision", model_id)
        return ModelCommand("invalid", tail)

    model_id = _resolve_model_id(rest, TEXT_MODELS)
    if model_id:
        return ModelCommand("set_text", model_id)

    return ModelCommand("invalid", rest)


def init_model_store(path: Path, default_text: str, default_vision: str) -> AiModelStore:
    global _store
    _store = AiModelStore(path, default_text, default_vision)
    return _store


def get_model_store() -> AiModelStore:
    if _store is None:
        raise RuntimeError("AiModelStore не инициализирован")
    return _store


class AiModelStore:
    def __init__(self, path: Path, default_text: str, default_vision: str) -> None:
        self._path = path
        self._default_text = default_text
        self._default_vision = default_vision
        self._text_model = default_text
        self._vision_model = default_vision
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
        text = raw.get("text_model")
        vision = raw.get("vision_model")
        if isinstance(text, str) and text.strip():
            self._text_model = text.strip()
        if isinstance(vision, str) and vision.strip():
            self._vision_model = vision.strip()

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {"text_model": self._text_model, "vision_model": self._vision_model},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    @property
    def text_model(self) -> str:
        return self._text_model

    @property
    def vision_model(self) -> str:
        return self._vision_model

    def set_text_model(self, model_id: str) -> None:
        self._text_model = model_id
        self._save()
        logger.info("Модель текста: %s", model_id)

    def set_vision_model(self, model_id: str) -> None:
        self._vision_model = model_id
        self._save()
        logger.info("Модель фото/видео: %s", model_id)

    def reset(self) -> None:
        self._text_model = self._default_text
        self._vision_model = self._default_vision
        self._save()


def _format_model_block(title: str, models: tuple[tuple[str, str], ...], current: str) -> list[str]:
    lines = [title]
    for model_id, label in models:
        mark = " ← сейчас" if model_id.casefold() == current.casefold() else ""
        lines.append(f"• {model_id} — {label}{mark}")
    return lines


def format_models_status(text_model: str, vision_model: str) -> str:
    lines = [
        "Текущие модели:",
        f"• Текст: {text_model}",
        f"• Фото/видео: {vision_model}",
        "",
        "Сменить:",
        "/ии текст deepseek-chat",
        "/ии фото claude-sonnet-4.6",
        "/ии список — все варианты",
    ]
    return "\n".join(lines)


def format_models_list(text_model: str, vision_model: str) -> str:
    lines = ["Модели, на которые можно переключиться:", ""]
    lines.extend(_format_model_block("Текст (/ии текст …):", TEXT_MODELS, text_model))
    lines.append("")
    lines.extend(_format_model_block("Фото и видео (/ии фото …):", VISION_MODELS, vision_model))
    lines.append("")
    lines.append("Примеры:")
    lines.append("/ии текст gpt-4o-mini")
    lines.append("/ии фото gpt-4o")
    return "\n".join(lines)


def format_invalid_model(name: str) -> str:
    token = name.strip()
    lower = token.casefold()
    text_match = next((mid for mid, _ in TEXT_MODELS if mid.casefold() == lower), None)
    vision_match = next((mid for mid, _ in VISION_MODELS if mid.casefold() == lower), None)

    if text_match and text_match not in {mid for mid, _ in VISION_MODELS}:
        return (
            f"«{token}» — только для текста, фото/видео ею не разберёшь.\n"
            f"Текст: /ии текст {text_match}\n"
            "Фото: /ии фото gemini-2.5-flash или /ии фото gpt-4o"
        )

    return (
        f"Модель «{token}» не в списке.\n"
        "Напиши /ии список — там все доступные варианты."
    )
