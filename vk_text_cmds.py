"""«артем кратко», «артем спорь», «артем нарисуй», «артем гиф», «артем мем», «артем цитата»."""

from __future__ import annotations

import re
from dataclasses import dataclass

_KRATKO_RE = re.compile(r"^кратко(?:\s+(?P<extra>.+))?$", re.IGNORECASE | re.DOTALL)
_SPOR_RE = re.compile(r"^спорь(?:\s+(?P<extra>.+))?$", re.IGNORECASE | re.DOTALL)
_TTS_WORD_RE = re.compile(r"\bозвучь\b", re.IGNORECASE)
_NARISUY_RE = re.compile(
    r"^нарисуй(?:\s+(?P<provider>kandinsky|horde|канди|хорд))?(?:\s+(?P<prompt>.+))?$",
    re.IGNORECASE | re.DOTALL,
)
_GIF_RE = re.compile(r"^(?:гиф|gif)(?:\s+(?P<query>.+))?$", re.IGNORECASE | re.DOTALL)
_MEM_RE = re.compile(r"^мем(?:\s+(?P<text>.+))?$", re.IGNORECASE | re.DOTALL)
_QUOTE_RE = re.compile(
    r"^(?:цитата|цитату|quote)(?:\s+(?P<style>.+))?$",
    re.IGNORECASE | re.DOTALL,
)

_IMAGE_STYLE_SUFFIX = ", meme style, funny, dramatic, high contrast"

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


@dataclass(frozen=True)
class NarisuyCommand:
    provider: str
    prompt: str


@dataclass(frozen=True)
class GifCommand:
    query: str = ""


@dataclass(frozen=True)
class MemeCommand:
    text: str = ""


@dataclass(frozen=True)
class QuoteCommand:
    style: str = ""


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


def _normalize_image_provider(raw: str | None) -> str:
    token = (raw or "").strip().casefold()
    if token in ("horde", "хорд"):
        return "horde"
    if token in ("kandinsky", "канди"):
        return "kandinsky"
    return "auto"


def parse_narisuy_command(prompt: str) -> NarisuyCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _NARISUY_RE.match(stripped)
    if not match:
        return None
    user_prompt = (match.group("prompt") or "").strip()
    if not user_prompt:
        user_prompt = "смешной мем с надписью"
    return NarisuyCommand(
        provider=_normalize_image_provider(match.group("provider")),
        prompt=user_prompt,
    )


def parse_gif_command(prompt: str) -> GifCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _GIF_RE.match(stripped)
    if not match:
        return None
    return GifCommand(query=(match.group("query") or "").strip())


def parse_meme_command(prompt: str) -> MemeCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _MEM_RE.match(stripped)
    if not match:
        return None
    return MemeCommand(text=(match.group("text") or "").strip())


def parse_quote_command(prompt: str) -> QuoteCommand | None:
    stripped = prompt.strip()
    if not stripped:
        return None
    match = _QUOTE_RE.match(stripped)
    if match:
        return QuoteCommand(style=(match.group("style") or "").strip())
    return None


def build_image_prompt(user_prompt: str) -> str:
    cleaned = user_prompt.strip()
    if not cleaned:
        return "funny meme" + _IMAGE_STYLE_SUFFIX
    lower = cleaned.casefold()
    if "meme" in lower or "мем" in lower:
        return cleaned
    return cleaned + _IMAGE_STYLE_SUFFIX


def build_narisuy_caption(prompt: str, provider_label: str) -> str:
    short = prompt.strip()[:60]
    return f"Вот, братан — {provider_label}: {short}"


def build_meme_caption(top: str, bottom: str) -> str:
    parts = [part.strip() for part in (top, bottom) if part.strip()]
    short = " / ".join(parts)[:70]
    return f"Вот мем, братан — {short}"


def build_quote_caption(author_name: str, date_label: str = "") -> str:
    short = author_name.strip()[:50] or "аноним"
    if date_label.strip():
        return f"Вот цитата, братан — {short} · {date_label.strip()}"
    return f"Вот цитата, братан — {short}"


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


def strip_tts_keyword(prompt: str) -> str:
    """Убирает «озвучь» из запроса, оставляя текст для ИИ."""
    cleaned, _ = split_prompt_flags(prompt)
    return cleaned


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
