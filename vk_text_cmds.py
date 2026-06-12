"""«артем кратко», «артем озвучь», «артем спорь»."""

from __future__ import annotations

import re
from dataclasses import dataclass

_KRATKO_RE = re.compile(r"^кратко(?:\s+(?P<extra>.+))?$", re.IGNORECASE | re.DOTALL)
_SPOR_RE = re.compile(r"^спорь(?:\s+(?P<extra>.+))?$", re.IGNORECASE | re.DOTALL)
_TTS_WORD_RE = re.compile(r"\bозвучь\b", re.IGNORECASE)

_SPOR_MODE_HINT = (
    "Режим «спорь»: жёстко спорь, не соглашайся, ломай его аргументы. "
    "Коротко 2–4 предложения — ответ пойдёт голосом."
)


@dataclass(frozen=True)
class KratkoCommand:
    extra: str = ""


@dataclass(frozen=True)
class SporCommand:
    extra: str = ""


def parse_kratko_command(prompt: str) -> KratkoCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _KRATKO_RE.match(stripped)
    if not match:
        return None
    return KratkoCommand(extra=(match.group("extra") or "").strip())


def parse_spor_command(prompt: str) -> SporCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _SPOR_RE.match(stripped)
    if not match:
        return None
    return SporCommand(extra=(match.group("extra") or "").strip())


def build_spor_extra_instruction(extra: str = "") -> str:
    if extra:
        return f"{_SPOR_MODE_HINT} Акцент: {extra}"
    return _SPOR_MODE_HINT


def build_kratko_prompt(source_text: str, extra: str = "") -> str:
    focus = f" Акцент: {extra}." if extra else ""
    return (
        f"Кратко перескажи суть в 2–4 предложениях — только главное, без воды.{focus} "
        f"Потом одна короткая колкость в стиле Артёма.\n\n"
        f"Текст для пересказа:\n{source_text}"
    )


def split_prompt_flags(prompt: str) -> tuple[str, bool]:
    """Убирает «озвучь» из запроса и возвращает флаг TTS."""
    stripped = prompt.strip()
    if not stripped:
        return "", False

    want_tts = bool(_TTS_WORD_RE.search(stripped))
    cleaned = _TTS_WORD_RE.sub(" ", stripped)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,:-—")
    if want_tts and not cleaned:
        cleaned = "скажи что-нибудь злое"
    return cleaned, want_tts
