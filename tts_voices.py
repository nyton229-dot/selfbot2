"""Команды /голос и «артем голос …» — выбор голоса для озвучки."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

VOICE_COMMANDS = ("/голос", "/voice", "/голоса")

# id, подпись, OpenAI/BotHub voice, edge-tts voice (все edge — разные)
TTS_VOICES: tuple[tuple[str, str, str, str], ...] = (
    ("дмитрий", "Дмитрий — мужской русский", "onyx", "ru-RU-DmitryNeural"),
    ("светлана", "Светлана — женский русский", "nova", "ru-RU-SvetlanaNeural"),
    ("остап", "Остап — мужской украинский", "echo", "uk-UA-OstapNeural"),
    ("полина", "Полина — женский украинский", "shimmer", "uk-UA-PolinaNeural"),
    ("даулет", "Даулет — мужской казахский", "alloy", "kk-KZ-DauletNeural"),
    ("айгуль", "Айгуль — женский казахский", "fable", "kk-KZ-AigulNeural"),
)

OMNIVOICE_INSTRUCTS: dict[str, str] = {
    "дмитрий": "male, young adult, moderate pitch, russian accent",
    "светлана": "female, young adult, moderate pitch, russian accent",
    "остап": "male, young adult, moderate pitch, russian accent",
    "полина": "female, young adult, moderate pitch, russian accent",
    "даулет": "male, young adult, moderate pitch, russian accent",
    "айгуль": "female, young adult, moderate pitch, russian accent",
}

# Старые id из прошлой версии списка
_LEGACY_VOICE_ALIASES: dict[str, str] = {
    "артём": "остап",
    "артем": "остап",
    "дарья": "полина",
    "софия": "полина",
    "нейтрал": "даулет",
}

_ARTEM_VOICE_RE = re.compile(r"^голос(?:\s+(?P<rest>.+))?$", re.IGNORECASE | re.DOTALL)

_store: TtsVoiceStore | None = None


@dataclass(frozen=True)
class VoiceCommand:
    action: str
    voice_id: str | None = None


def _voice_index() -> dict[str, tuple[str, str, str, str]]:
    index: dict[str, tuple[str, str, str, str]] = {}
    for voice_id, label, openai_voice, edge_voice in TTS_VOICES:
        entry = (voice_id, label, openai_voice, edge_voice)
        for alias in (voice_id, openai_voice):
            index[alias.casefold()] = entry
    return index


_VOICE_INDEX = _voice_index()


def is_voice_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in VOICE_COMMANDS:
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def is_artem_voice_command(prompt: str) -> bool:
    return _ARTEM_VOICE_RE.match(prompt.strip()) is not None


def _strip_slash_command(text: str) -> str:
    stripped = text.strip()
    lower = stripped.casefold()
    for prefix in VOICE_COMMANDS:
        cmd = prefix.casefold()
        if lower == cmd:
            return ""
        if lower.startswith(cmd + " "):
            return stripped[len(prefix) :].strip()
    return stripped


def resolve_voice_id(raw: str) -> str | None:
    token = raw.strip()
    if not token:
        return None
    key = token.casefold()
    legacy = _LEGACY_VOICE_ALIASES.get(key)
    if legacy:
        key = legacy.casefold()
    entry = _VOICE_INDEX.get(key)
    if entry is None:
        return None
    return entry[0]


def resolve_openai_voice(voice_id: str) -> str:
    entry = _VOICE_INDEX.get(voice_id.strip().casefold())
    if entry is None:
        return voice_id
    return entry[2]


def resolve_edge_voice(voice_id: str) -> str:
    entry = _VOICE_INDEX.get(voice_id.strip().casefold())
    if entry is None:
        return "ru-RU-DmitryNeural"
    return entry[3]


def resolve_omnivoice_instruct(voice_id: str) -> str:
    resolved = resolve_voice_id(voice_id) or voice_id.strip().casefold()
    return OMNIVOICE_INSTRUCTS.get(resolved, "male, young adult, moderate pitch, russian accent")


def voice_label(voice_id: str) -> str:
    entry = _VOICE_INDEX.get(voice_id.strip().casefold())
    if entry is None:
        return voice_id
    return entry[1]


def _parse_voice_rest(rest: str) -> VoiceCommand:
    stripped = rest.strip()
    if not stripped:
        return VoiceCommand("show")

    lower = stripped.casefold()
    if lower in ("список", "list", "voices", "голоса"):
        return VoiceCommand("list")

    voice_id = resolve_voice_id(stripped)
    if voice_id:
        return VoiceCommand("set", voice_id)

    return VoiceCommand("invalid", stripped)


def parse_voice_command(text: str) -> VoiceCommand | None:
    stripped = text.strip()
    if is_voice_command(stripped):
        return _parse_voice_rest(_strip_slash_command(stripped))
    match = _ARTEM_VOICE_RE.match(stripped)
    if match is None:
        return None
    return _parse_voice_rest(match.group("rest") or "")


def init_voice_store(path: Path, default_voice: str) -> TtsVoiceStore:
    global _store
    default_id = resolve_voice_id(default_voice) or "дмитрий"
    _store = TtsVoiceStore(path, default_id)
    return _store


def get_voice_store() -> TtsVoiceStore:
    if _store is None:
        raise RuntimeError("TtsVoiceStore не инициализирован")
    return _store


class TtsVoiceStore:
    def __init__(self, path: Path, default_voice_id: str) -> None:
        self._path = path
        self._default_voice_id = default_voice_id
        self._voice_id = default_voice_id
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
        voice = raw.get("voice_id")
        if isinstance(voice, str):
            resolved = resolve_voice_id(voice)
            if resolved:
                self._voice_id = resolved

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"voice_id": self._voice_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @property
    def voice_id(self) -> str:
        return self._voice_id

    @property
    def openai_voice(self) -> str:
        return resolve_openai_voice(self._voice_id)

    @property
    def edge_voice(self) -> str:
        return resolve_edge_voice(self._voice_id)

    def set_voice(self, voice_id: str) -> None:
        resolved = resolve_voice_id(voice_id)
        if not resolved:
            raise ValueError(voice_id)
        self._voice_id = resolved
        self._save()
        logger.info("Голос TTS: %s (%s)", resolved, voice_label(resolved))


def format_voice_status(voice_id: str) -> str:
    lines = [
        f"Сейчас: {voice_label(voice_id)} ({voice_id})",
        "",
        "Сменить:",
        "/голос дмитрий",
        "/голос остап",
        "или «артем голос полина»",
        "/голос список — все варианты",
    ]
    return "\n".join(lines)


def format_voice_list(voice_id: str) -> str:
    lines = ["Голоса для озвучки:", ""]
    for item_id, label, openai_voice, _edge_voice in TTS_VOICES:
        mark = " ← сейчас" if item_id == voice_id else ""
        lines.append(f"• {item_id} — {label}{mark}")
        lines.append(f"  ({openai_voice})")
    lines.extend(
        [
            "",
            "Сменить:",
            "/голос дмитрий",
            "/голос остап",
            "артем голос айгуль",
        ]
    )
    return "\n".join(lines)


def format_invalid_voice(name: str) -> str:
    return (
        f"Голос «{name}» не в списке.\n"
        "Напиши /голос список — там все варианты."
    )


def apply_voice_command(command: VoiceCommand, store: TtsVoiceStore) -> str:
    if command.action == "list":
        return format_voice_list(store.voice_id)
    if command.action == "set" and command.voice_id:
        store.set_voice(command.voice_id)
        return f"Голос: {voice_label(store.voice_id)} ({store.voice_id})"
    if command.action == "invalid":
        return format_invalid_voice(command.voice_id or "?")
    return format_voice_status(store.voice_id)
