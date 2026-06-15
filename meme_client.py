"""Локальный генератор мемов (Pillow) — без API."""

from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

from vk_reply import get_reply_message, reply_message_text

logger = logging.getLogger(__name__)

_MAX_IMAGE_BYTES = 8 * 1024 * 1024
_FONT_CANDIDATES = (
    Path(__file__).resolve().parent / "fonts" / "Impact.ttf",
    Path("C:/Windows/Fonts/impact.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
)

_CAPTION_SEPARATORS = (" / ", " | ", " // ", "\n")
_RU_SPLIT_RE = re.compile(r"\s+(?:а|но)\s+", re.IGNORECASE)


def split_meme_caption(text: str) -> tuple[str, str]:
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return "", ""

    for separator in _CAPTION_SEPARATORS:
        if separator in cleaned:
            top, bottom = cleaned.split(separator, 1)
            return top.strip(), bottom.strip()

    match = _RU_SPLIT_RE.search(cleaned)
    if match:
        top = cleaned[: match.start()].strip()
        bottom = cleaned[match.end() :].strip()
        if top and bottom:
            return top, bottom

    return "", cleaned


def resolve_meme_caption(raw_text: str, message_data: dict[str, Any] | None) -> tuple[str, str]:
    source = raw_text.strip()
    if not source:
        reply = get_reply_message(message_data)
        if reply is not None:
            source = reply_message_text(reply).strip()
            if source.startswith("["):
                source = ""
    top, bottom = split_meme_caption(source)
    if not top and not bottom:
        raise RuntimeError(
            "Напиши текст мема: «артем мем когда понедельник / а ты на работе» "
            "или ответь на фото с подписью."
        )
    return top, bottom


def resolve_meme_photo_url(message_data: dict[str, Any] | None, vk: Any) -> str:
    if not message_data:
        raise RuntimeError("Ответь на фото и напиши «артем мем …».")

    reply = get_reply_message(message_data)
    if isinstance(reply, dict):
        url = _first_photo_url(reply.get("attachments"), vk)
        if url:
            return url

    url = _first_photo_url(message_data.get("attachments"), vk)
    if url:
        return url

    raise RuntimeError(
        "Нужна картинка — ответь на фото или приложи фото к «артем мем …»."
    )


def _first_photo_url(attachments: Any, vk: Any) -> str | None:
    from vk_media import get_photo_entries

    if not isinstance(attachments, list) or not attachments:
        return None
    entries = get_photo_entries({"attachments": attachments}, vk)
    if not entries:
        return None
    return entries[0][0]


def download_image_bytes(url: str, session: requests.Session | None = None) -> bytes:
    headers = {"User-Agent": "lpbot/1.0 (meme generator)"}
    if session is None:
        response = requests.get(url, headers=headers, timeout=45)
    else:
        response = session.get(url, headers=headers, timeout=45)
    response.raise_for_status()
    content = response.content
    if not content:
        raise RuntimeError("Пустой файл картинки")
    if len(content) > _MAX_IMAGE_BYTES:
        raise RuntimeError("Картинка слишком большая для мема")
    return content


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                logger.debug("Шрифт не загрузился: %s", path)
    return ImageFont.load_default()


def _fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    *,
    max_size: int = 72,
    min_size: int = 14,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    upper = text.upper()
    for size in range(max_size, min_size - 1, -2):
        font = _load_font(size)
        bbox = draw.multiline_textbbox((0, 0), upper, font=font, spacing=4)
        if bbox[2] - bbox[0] <= max_width:
            return font
    return _load_font(min_size)


def _wrap_text(text: str, font: ImageFont.ImageFont, max_width: int, draw: ImageDraw.ImageDraw) -> str:
    words = text.upper().split()
    if not words:
        return ""

    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines[:3])


def _draw_band(
    draw: ImageDraw.ImageDraw,
    text: str,
    *,
    image_width: int,
    image_height: int,
    top: bool,
) -> None:
    if not text.strip():
        return

    margin = max(12, int(image_width * 0.05))
    max_text_width = image_width - margin * 2
    font = _fit_font(draw, text, max_text_width)
    wrapped = _wrap_text(text, font, max_text_width, draw)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4, align="center")
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    x = image_width // 2
    if top:
        y = margin + text_height // 2
    else:
        y = image_height - margin - text_height // 2

    stroke = max(2, int(image_width * 0.004))
    draw.multiline_text(
        (x, y),
        wrapped,
        font=font,
        fill="white",
        stroke_width=stroke,
        stroke_fill="black",
        anchor="mm",
        align="center",
        spacing=4,
    )
    logger.debug("Meme band %s: %r (%sx%s)", "top" if top else "bottom", wrapped[:40], text_width, text_height)


def make_meme(image_bytes: bytes, top_text: str, bottom_text: str) -> bytes:
    with Image.open(BytesIO(image_bytes)) as opened:
        image = opened.convert("RGB")
        draw = ImageDraw.Draw(image)
        _draw_band(
            draw,
            top_text,
            image_width=image.width,
            image_height=image.height,
            top=True,
        )
        _draw_band(
            draw,
            bottom_text,
            image_width=image.width,
            image_height=image.height,
            top=False,
        )

        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=90, optimize=True)
        return buffer.getvalue()


def build_meme(
    *,
    message_data: dict[str, Any] | None,
    vk: Any,
    raw_text: str,
    session: requests.Session | None = None,
) -> tuple[bytes, str, str]:
    top, bottom = resolve_meme_caption(raw_text, message_data)
    photo_url = resolve_meme_photo_url(message_data, vk)
    image_bytes = download_image_bytes(photo_url, session)
    meme_bytes = make_meme(image_bytes, top, bottom)
    preview = " / ".join(part for part in (top, bottom) if part)[:80]
    logger.info("Мем готов: %r", preview)
    return meme_bytes, top, bottom
