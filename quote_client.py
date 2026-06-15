"""Карточка «ЦИТАТЫ ВЕЛИКИХ ЛЮДЕЙ» — стиль Studio Petukh."""

from __future__ import annotations

import logging
import re
import textwrap
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image, ImageDraw, ImageFont

from meme_client import download_image_bytes
from vk_avatar import fetch_user_avatar
from vk_reply import get_reply_message, reply_message_text

logger = logging.getLogger(__name__)

W = 1100
H = 620
_HEADER_H = 54
_FOOTER_H = 42
_MAX_TEXT = 700

_BLACK = (0, 0, 0)
_WHITE = (255, 255, 255)

_FONT_STENCIL = (
    Path("C:/Windows/Fonts/impact.ttf"),
    Path("C:/Windows/Fonts/ariblk.ttf"),
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)
_FONT_TITLE = (
    Path("C:/Windows/Fonts/arialbd.ttf"),
    Path("C:/Windows/Fonts/segoeuib.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
)
_FONT_SMALL = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("C:/Windows/Fonts/segoeui.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)
_FONT_TINY = (
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("C:/Windows/Fonts/cour.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
)

_BOT_TEXT_RE = re.compile(
    r"^(?:"
    r"артем\s+)?(?:"
    r"цитата|цитату|quote|гиф|gif|мем|нарисуй|кратко|спорь|"
    r"озвучь|голос|ответь|reply"
    r")(?:\s|$)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class QuoteCard:
    author_name: str
    text: str
    date_label: str
    user_id: int
    message_ts: int
    avatar_bytes: bytes | None = None


def _font(paths: tuple[Path, ...], size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in paths:
        if path.is_file():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def _stamp(ts: int) -> str:
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%H:%M:%S %d-%m-%y")


def _generated_stamp() -> str:
    return datetime.now().strftime("GENERATED: %H:%M:%S %d-%m-%y")


def _display_date(ts: int) -> str:
    dt = datetime.fromtimestamp(ts)
    return dt.strftime("%d.%m.%Y %H:%M")


def _user_name(vk: Any, uid: int) -> str:
    users = vk.users.get(user_ids=uid, fields="first_name,last_name")
    if not users:
        return f"ID{uid}"
    user = users[0]
    return f"{user.get('first_name', '')} {user.get('last_name', '')}".strip() or f"ID{uid}"


def _avatar_bytes(vk: Any, uid: int) -> bytes | None:
    try:
        payload = fetch_user_avatar(vk, uid)
    except LookupError:
        return None
    if not payload.image_url:
        return None
    try:
        return download_image_bytes(payload.image_url)
    except requests.RequestException:
        return None


def _validate_quote_source(reply: dict[str, Any]) -> str:
    text = reply_message_text(reply).strip()
    if not text or text.startswith("["):
        raise RuntimeError("В том сообщении нет текста — ответь на слова человека.")
    if _BOT_TEXT_RE.match(text):
        raise RuntimeError(
            "Ты ответил на команду бота. Ответь на обычное сообщение — потом «артем цитата»."
        )
    lowered = text.casefold()
    if lowered.startswith("не вышло") or lowered.startswith("вот цитата"):
        raise RuntimeError("Ответь на нормальное сообщение, а не на ответ бота.")
    return text


def build_quote_from_reply(*, message_data: dict[str, Any] | None, vk: Any) -> QuoteCard:
    reply = get_reply_message(message_data)
    if not isinstance(reply, dict):
        raise RuntimeError("Ответь на сообщение и напиши «артем цитата».")

    uid = reply.get("from_id")
    if not isinstance(uid, int) or uid <= 0:
        raise RuntimeError("Не понял, кто автор сообщения.")

    text = _validate_quote_source(reply)
    if len(text) > _MAX_TEXT:
        text = text[: _MAX_TEXT - 1].rstrip() + "…"

    ts = reply.get("date")
    if not isinstance(ts, int):
        raise RuntimeError("В сообщении нет даты.")

    return QuoteCard(
        author_name=_user_name(vk, uid),
        text=text,
        date_label=_display_date(ts),
        user_id=uid,
        message_ts=ts,
        avatar_bytes=_avatar_bytes(vk, uid),
    )


def _square_avatar(img: Image.Image, size: int) -> Image.Image:
    return img.convert("RGB").resize((size, size), Image.Resampling.LANCZOS)


def _circle_avatar(img: Image.Image, size: int) -> Image.Image:
    prepared = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(prepared, (0, 0), mask)
    return out


def _wrap_plain(text: str, font: ImageFont.ImageFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    words = textwrap.dedent(text).strip().split()
    if not words:
        return ["…"]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word]) if current else word
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_w or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _fit_plain_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    max_h: int,
    *,
    max_size: int = 56,
    min_size: int = 20,
) -> tuple[ImageFont.ImageFont, list[str]]:
    for size in range(max_size, min_size - 1, -2):
        font = _font(_FONT_TITLE, size)
        lines = _wrap_plain(text, font, max_w, draw)
        bbox = draw.multiline_textbbox((0, 0), "\n".join(lines), font=font, spacing=10, align="center")
        if bbox[2] - bbox[0] <= max_w and bbox[3] - bbox[1] <= max_h:
            return font, lines
    font = _font(_FONT_TITLE, min_size)
    return font, _wrap_plain(text, font, max_w, draw)


def _wrap_upper(text: str, font: ImageFont.ImageFont, max_w: int, draw: ImageDraw.ImageDraw) -> list[str]:
    upper = text.upper()
    words = upper.split()
    if not words:
        return ["…"]
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word]) if current else word
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_w or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]
    if current:
        lines.append(" ".join(current))
    return lines


def _fit_quote_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    max_h: int,
) -> tuple[ImageFont.ImageFont, list[str]]:
    for size in range(34, 14, -2):
        font = _font(_FONT_STENCIL, size)
        lines = _wrap_upper(text, font, max_w - 40, draw)
        body = "\n".join(lines)
        bbox = draw.multiline_textbbox((0, 0), body, font=font, spacing=8)
        if bbox[2] - bbox[0] <= max_w and bbox[3] - bbox[1] <= max_h:
            return font, lines
    font = _font(_FONT_STENCIL, 14)
    return font, _wrap_upper(text, font, max_w - 40, draw)


def _paste_rotated(
    base: Image.Image,
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    anchor_xy: tuple[int, int],
) -> None:
    pad = Image.new("RGBA", (600, 40), (0, 0, 0, 0))
    ImageDraw.Draw(pad).text((0, 0), text, font=font, fill=(*fill, 255))
    rotated = pad.rotate(90, expand=True)
    base.paste(rotated, anchor_xy, rotated)


def render_petukh_image(quote: QuoteCard) -> bytes:
    img = Image.new("RGB", (W, H), _BLACK)
    draw = ImageDraw.Draw(img)

    # --- шапка ---
    draw.rectangle((0, 0, W, _HEADER_H), fill=_WHITE)
    title_font = _font(_FONT_TITLE, 24)
    tiny_font = _font(_FONT_TINY, 11)
    draw.text((W // 2, _HEADER_H // 2), "ЦИТАТЫ ВЕЛИКИХ ЛЮДЕЙ", font=title_font, fill=_BLACK, anchor="mm")
    draw.text((W - 14, _HEADER_H - 8), "BY STUDIO PETUKH", font=tiny_font, fill=_BLACK, anchor="rb")

    body_top = _HEADER_H + 8
    body_bot = H - _FOOTER_H - 70
    side_pad = 70

    # --- вертикальная дата слева ---
    side_font = _font(_FONT_TINY, 12)
    _paste_rotated(img, _stamp(quote.message_ts), font=side_font, fill=_WHITE, anchor_xy=(18, body_top + 20))

    # --- цитата по центру ---
    quote_left = side_pad + 30
    quote_right = W - side_pad
    quote_w = quote_right - quote_left
    quote_h = body_bot - body_top - 20

    q_font, q_lines = _fit_quote_font(draw, quote.text, quote_w, quote_h)
    open_mark = "«"
    close_mark = "»"
    body_lines = [open_mark + q_lines[0]] + q_lines[1:-1] + [q_lines[-1] + close_mark if q_lines else open_mark + close_mark]
    if len(q_lines) == 1:
        body_lines = [open_mark + q_lines[0] + close_mark]

    body_text = "\n".join(body_lines)
    bbox = draw.multiline_textbbox((0, 0), body_text, font=q_font, spacing=10)
    text_h = bbox[3] - bbox[1]
    text_y = body_top + max(30, (quote_h - text_h) // 2)

    draw.multiline_text(
        ((quote_left + quote_right) // 2, text_y),
        body_text,
        font=q_font,
        fill=_WHITE,
        spacing=10,
        align="center",
        anchor="ma",
    )

    # --- автор: ава + © + имя ---
    author_y = H - _FOOTER_H - 58
    av_size = 46
    av_x = 36
    if quote.avatar_bytes:
        with Image.open(BytesIO(quote.avatar_bytes)) as raw:
            av = _square_avatar(raw, av_size)
            img.paste(av, (av_x, author_y))
            draw = ImageDraw.Draw(img)
    else:
        draw.rectangle((av_x, author_y, av_x + av_size, author_y + av_size), fill=(40, 40, 40))

    author_font = _font(_FONT_STENCIL, 22)
    name_upper = quote.author_name.upper()
    name_x = av_x + av_size + 12
    draw.text((name_x, author_y + 4), "©", font=author_font, fill=_WHITE)
    copy_w = draw.textbbox((0, 0), "©", font=author_font)[2]
    draw.text((name_x + copy_w + 8, author_y + 6), name_upper, font=author_font, fill=_WHITE)

    # --- подвал ---
    footer_y = H - 24
    foot_font = _font(_FONT_TINY, 11)
    draw.text((24, footer_y), f"ID{quote.user_id}", font=foot_font, fill=_WHITE, anchor="ls")
    draw.text((W // 2, footer_y), "AUTHENTIC QUOTE: TRUE", font=foot_font, fill=_WHITE, anchor="ms")
    draw.text((W - 24, footer_y), _generated_stamp(), font=foot_font, fill=_WHITE, anchor="rs")

    buf = BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_cyber_image(quote: QuoteCard) -> bytes:
    w, h = 1200, 675
    img = Image.new("RGBA", (w, h), (6, 10, 24, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    for y in range(h):
        t = y / max(h - 1, 1)
        draw.line((0, y, w, y), fill=(int(6 + 12 * t), int(10 + 24 * t), int(24 + 54 * t), 255))

    for x in range(0, w, 92):
        draw.line((x, 0, x, h), fill=(40, 130, 220, 28), width=1)
    for y in range(0, h, 78):
        draw.line((0, y, w, y), fill=(40, 130, 220, 20), width=1)
    for cx, cy, rx, ry, alpha in ((850, 300, 260, 130, 55), (240, 540, 190, 90, 42)):
        draw.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(26, 105, 210, alpha))

    draw.rounded_rectangle((55, 55, w - 55, h - 55), radius=28, outline=(115, 190, 255, 150), width=3)
    draw.line((82, 104, w - 82, 104), fill=(210, 235, 255, 190), width=2)
    draw.line((82, h - 104, w - 82, h - 104), fill=(210, 235, 255, 190), width=2)

    av_size = 190
    av_x, av_y = 135, 175
    draw.ellipse((av_x - 8, av_y - 8, av_x + av_size + 8, av_y + av_size + 8), outline=(230, 245, 255, 220), width=4)
    if quote.avatar_bytes:
        with Image.open(BytesIO(quote.avatar_bytes)) as raw:
            avatar = _circle_avatar(raw, av_size)
            img.paste(avatar, (av_x, av_y), avatar)
    else:
        draw.ellipse((av_x, av_y, av_x + av_size, av_y + av_size), fill=(50, 65, 85, 255))

    name_font = _font(_FONT_TITLE, 34)
    small_font = _font(_FONT_SMALL, 24)
    draw.text((av_x + av_size // 2, av_y + av_size + 36), quote.author_name, font=name_font, fill=_WHITE, anchor="mt")
    draw.text((av_x + av_size // 2, av_y + av_size + 82), quote.date_label, font=small_font, fill=(170, 190, 210), anchor="mt")

    q_left, q_top, q_right, q_bottom = 430, 155, 1090, 520
    q_font, q_lines = _fit_plain_font(draw, quote.text, q_right - q_left, q_bottom - q_top, max_size=52)
    text = "\n".join(q_lines)
    bbox = draw.multiline_textbbox((0, 0), text, font=q_font, spacing=10, align="center")
    ty = q_top + ((q_bottom - q_top) - (bbox[3] - bbox[1])) // 2
    mark = _font(_FONT_TITLE, 92)
    draw.text((q_left - 30, q_top - 50), "“", font=mark, fill=(245, 250, 255))
    draw.multiline_text(((q_left + q_right) // 2, ty), text, font=q_font, fill=_WHITE, spacing=10, align="center", anchor="ma")
    draw.text((q_right - 20, q_bottom - 40), "”", font=mark, fill=(245, 250, 255))

    badge_font = _font(_FONT_TINY, 18)
    draw.text((w - 80, h - 70), ">%+ default xP_", font=badge_font, fill=(125, 185, 255), anchor="rs")

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_neon_image(quote: QuoteCard) -> bytes:
    w, h = 1200, 675
    img = Image.new("RGBA", (w, h), (12, 3, 22, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    draw.rectangle((0, 0, w, h), fill=(12, 3, 22, 255))
    draw.ellipse((720, -120, 1280, 330), fill=(170, 28, 190, 70))
    draw.ellipse((-120, 390, 420, 800), fill=(20, 170, 230, 62))
    draw.rounded_rectangle((70, 70, w - 70, h - 70), radius=38, outline=(255, 62, 210, 180), width=4)
    draw.rounded_rectangle((90, 90, w - 90, h - 90), radius=30, outline=(40, 230, 255, 135), width=2)

    av_size = 150
    av_x, av_y = 120, 128
    if quote.avatar_bytes:
        with Image.open(BytesIO(quote.avatar_bytes)) as raw:
            avatar = _circle_avatar(raw, av_size)
            img.paste(avatar, (av_x, av_y), avatar)
    else:
        draw.ellipse((av_x, av_y, av_x + av_size, av_y + av_size), fill=(48, 40, 70, 255))

    name_font = _font(_FONT_TITLE, 30)
    meta_font = _font(_FONT_TINY, 18)
    draw.text((120, 315), quote.author_name.upper(), font=name_font, fill=(255, 245, 255))
    draw.text((120, 360), quote.date_label, font=meta_font, fill=(125, 235, 255))
    draw.line((120, 410, 335, 410), fill=(255, 62, 210, 180), width=3)

    q_left, q_top, q_right, q_bottom = 410, 145, 1080, 540
    q_font, q_lines = _fit_plain_font(draw, quote.text, q_right - q_left, q_bottom - q_top, max_size=58)
    text = "\n".join(q_lines)
    bbox = draw.multiline_textbbox((0, 0), text, font=q_font, spacing=12, align="center")
    ty = q_top + ((q_bottom - q_top) - (bbox[3] - bbox[1])) // 2

    for dx, dy, color in ((3, 0, (255, 40, 210)), (-3, 0, (30, 220, 255))):
        draw.multiline_text(((q_left + q_right) // 2 + dx, ty + dy), text, font=q_font, fill=color, spacing=12, align="center", anchor="ma")
    draw.multiline_text(((q_left + q_right) // 2, ty), text, font=q_font, fill=_WHITE, spacing=12, align="center", anchor="ma")

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def render_minimal_image(quote: QuoteCard) -> bytes:
    w, h = 1100, 620
    img = Image.new("RGB", (w, h), (242, 238, 228))
    draw = ImageDraw.Draw(img)
    draw.rectangle((42, 42, w - 42, h - 42), outline=(20, 20, 20), width=4)
    draw.rectangle((62, 62, w - 62, h - 62), outline=(20, 20, 20), width=1)

    title = _font(_FONT_TITLE, 28)
    draw.text((w // 2, 96), "АРХИВ ВЕЛИКИХ ЦИТАТ", font=title, fill=(18, 18, 18), anchor="mm")
    draw.line((150, 132, w - 150, 132), fill=(18, 18, 18), width=2)

    q_left, q_top, q_right, q_bottom = 170, 175, 930, 420
    q_font, q_lines = _fit_plain_font(draw, quote.text, q_right - q_left, q_bottom - q_top, max_size=46)
    text = "«" + "\n".join(q_lines) + "»"
    bbox = draw.multiline_textbbox((0, 0), text, font=q_font, spacing=10, align="center")
    ty = q_top + ((q_bottom - q_top) - (bbox[3] - bbox[1])) // 2
    draw.multiline_text((w // 2, ty), text, font=q_font, fill=(18, 18, 18), spacing=10, align="center", anchor="ma")

    av_size = 72
    av_x, av_y = 160, 470
    if quote.avatar_bytes:
        with Image.open(BytesIO(quote.avatar_bytes)) as raw:
            avatar = _circle_avatar(raw, av_size)
            img = img.convert("RGBA")
            img.paste(avatar, (av_x, av_y), avatar)
            draw = ImageDraw.Draw(img)
    else:
        draw.ellipse((av_x, av_y, av_x + av_size, av_y + av_size), fill=(30, 30, 30))

    author = _font(_FONT_TITLE, 24)
    meta = _font(_FONT_SMALL, 18)
    draw.text((av_x + av_size + 22, av_y + 8), quote.author_name, font=author, fill=(18, 18, 18))
    draw.text((av_x + av_size + 22, av_y + 42), quote.date_label, font=meta, fill=(80, 80, 80))
    draw.text((w - 130, h - 72), "AUTHENTIC: TRUE", font=_font(_FONT_TINY, 16), fill=(18, 18, 18), anchor="rm")

    buf = BytesIO()
    img.convert("RGB").save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _normalize_style(style: str) -> str:
    token = style.strip().casefold()
    if token in ("", "великие", "петух", "petukh", "studio", "classic", "классика"):
        return "petukh"
    if token in ("кибер", "cyber", "киберпанк", "profile"):
        return "cyber"
    if token in ("неон", "neon", "ночь"):
        return "neon"
    if token in ("минимал", "minimal", "газета", "архив"):
        return "minimal"
    return "petukh"


def render_quote_image(quote: QuoteCard, style: str = "") -> bytes:
    normalized = _normalize_style(style)
    if normalized == "cyber":
        return render_cyber_image(quote)
    if normalized == "neon":
        return render_neon_image(quote)
    if normalized == "minimal":
        return render_minimal_image(quote)
    return render_petukh_image(quote)


def build_quote_image(
    *,
    message_data: dict[str, Any] | None,
    vk: Any,
    style: str = "",
) -> tuple[bytes, QuoteCard]:
    quote = build_quote_from_reply(message_data=message_data, vk=vk)
    return render_quote_image(quote, style), quote
