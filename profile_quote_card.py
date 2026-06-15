"""Генератор картинки-профиля/цитаты для VK-бота через Pillow.

Если рядом есть файлы, использует их:
- bg.png
- avatar.jpg
- small_avatar.jpg
- line.png
- handwritten.ttf
- main.ttf

Если файлов нет, рисует тестовый фон/аватарки и берёт системные шрифты Windows.

Запуск:
    python profile_quote_card.py
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent

CANVAS_SIZE = (1920, 1080)
MAIN_AVATAR_CENTER = (450, 450)
MAIN_AVATAR_RADIUS = 250
SMALL_AVATAR_CENTER = (1700, 950)
SMALL_AVATAR_RADIUS = 50
QUOTE_BOX = (900, 300, 1700, 800)  # left, top, right, bottom


def optional_file(name: str) -> Path | None:
    path = BASE_DIR / name
    return path if path.is_file() else None


def load_font(name: str, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    local = optional_file(name)
    if local is not None:
        return ImageFont.truetype(str(local), size=size)

    fallback_names = {
        "handwritten.ttf": (
            Path("C:/Windows/Fonts/segoepr.ttf"),
            Path("C:/Windows/Fonts/comic.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
        ),
        "main.ttf": (
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/segoeuib.ttf"),
            Path("C:/Windows/Fonts/impact.ttf"),
        ),
    }
    for path in fallback_names.get(name, (Path("C:/Windows/Fonts/arial.ttf"),)):
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)

    return ImageFont.load_default()


def make_placeholder_avatar(radius: int, label: str = "A") -> Image.Image:
    size = radius * 2
    image = Image.new("RGBA", (size, size), (42, 50, 64, 255))
    draw = ImageDraw.Draw(image)
    draw.ellipse((0, 0, size - 1, size - 1), fill=(42, 50, 64, 255), outline=(230, 235, 245, 255), width=6)
    font = load_font("main.ttf", max(18, radius))
    draw.text((radius, radius), label[:1].upper(), font=font, fill="white", anchor="mm")
    return image


def circle_crop(image_path: Path | None, radius: int, label: str = "A") -> Image.Image:
    if image_path is None:
        return make_placeholder_avatar(radius, label)

    size = radius * 2
    image = Image.open(image_path).convert("RGBA")
    image = image.resize((size, size), Image.Resampling.LANCZOS)

    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size - 1, size - 1), fill=255)

    result = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    result.paste(image, (0, 0), mask)
    return result


def make_fallback_background() -> Image.Image:
    canvas = Image.new("RGBA", CANVAS_SIZE, (4, 8, 18, 255))
    draw = ImageDraw.Draw(canvas, "RGBA")

    for y in range(CANVAS_SIZE[1]):
        t = y / (CANVAS_SIZE[1] - 1)
        color = (
            int(5 + 8 * t),
            int(10 + 20 * t),
            int(24 + 42 * t),
            255,
        )
        draw.line((0, y, CANVAS_SIZE[0], y), fill=color)

    # Простая "кибер"-сетка, чтобы скрипт был виден без bg.png.
    for x in range(0, CANVAS_SIZE[0], 120):
        draw.line((x, 0, x, CANVAS_SIZE[1]), fill=(40, 110, 180, 35), width=1)
    for y in range(0, CANVAS_SIZE[1], 90):
        draw.line((0, y, CANVAS_SIZE[0], y), fill=(40, 110, 180, 28), width=1)
    for x in range(80, CANVAS_SIZE[0], 180):
        for y in range(80, CANVAS_SIZE[1], 150):
            draw.ellipse((x - 4, y - 4, x + 4, y + 4), fill=(120, 200, 255, 80))

    return canvas


def make_fallback_line(width: int = 1460, height: int = 42) -> Image.Image:
    line = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(line)
    y = height // 2
    cx = width // 2
    draw.line((0, y, cx - 80, y), fill="white", width=3)
    draw.line((cx + 80, y, width, y), fill="white", width=3)
    draw.polygon([(cx, y - 18), (cx + 18, y), (cx, y + 18), (cx - 18, y)], outline="white")
    for off in (55, 95):
        draw.ellipse((cx - off - 8, y - 8, cx - off + 8, y + 8), fill="white")
        draw.ellipse((cx + off - 8, y - 8, cx + off + 8, y + 8), fill="white")
    return line


def paste_center(base: Image.Image, overlay: Image.Image, center: tuple[int, int]) -> None:
    x = center[0] - overlay.width // 2
    y = center[1] - overlay.height // 2
    base.paste(overlay, (x, y), overlay)


def wrap_text_to_width(
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    words = textwrap.dedent(text).strip().split()
    if not words:
        return [""]

    lines: list[str] = []
    current: list[str] = []

    for word in words:
        candidate = " ".join(current + [word]) if current else word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current.append(word)
        else:
            lines.append(" ".join(current))
            current = [word]

    if current:
        lines.append(" ".join(current))

    return lines


def fit_quote_font(
    text: str,
    font_name: str,
    box: tuple[int, int, int, int],
    draw: ImageDraw.ImageDraw,
    *,
    max_size: int = 100,
    min_size: int = 24,
    line_spacing: int = 12,
) -> tuple[ImageFont.FreeTypeFont, list[str], tuple[int, int]]:
    left, top, right, bottom = box
    max_width = right - left
    max_height = bottom - top

    size = max_size
    while size >= min_size:
        font = load_font(font_name, size)
        lines = wrap_text_to_width(text, font, max_width, draw)
        rendered = "\n".join(lines)
        bbox = draw.multiline_textbbox(
            (0, 0),
            rendered,
            font=font,
            spacing=line_spacing,
            align="center",
        )
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        if text_width <= max_width and text_height <= max_height:
            return font, lines, (text_width, text_height)

        size -= 2

    font = load_font(font_name, min_size)
    lines = wrap_text_to_width(text, font, max_width, draw)
    bbox = draw.multiline_textbbox(
        (0, 0),
        "\n".join(lines),
        font=font,
        spacing=line_spacing,
        align="center",
    )
    return font, lines, (bbox[2] - bbox[0], bbox[3] - bbox[1])


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    center_x: int,
    y: int,
    font: ImageFont.ImageFont,
    fill: str = "white",
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    draw.text((center_x - text_width // 2, y), text, font=font, fill=fill)


def generate_card(
    *,
    username: str,
    date_text: str,
    quote_text: str,
    system_text: str = ">%+ default xP_",
    output: str = "profile_quote.png",
) -> Path:
    bg_path = optional_file("bg.png")
    line_path = optional_file("line.png")
    avatar_path = optional_file("avatar.jpg")
    small_avatar_path = optional_file("small_avatar.jpg")

    if bg_path is None:
        canvas = make_fallback_background()
    else:
        canvas = Image.open(bg_path).convert("RGBA").resize(CANVAS_SIZE, Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    # Декоративные линии сверху и снизу.
    line = Image.open(line_path).convert("RGBA") if line_path is not None else make_fallback_line()
    top_x = (CANVAS_SIZE[0] - line.width) // 2
    canvas.paste(line, (top_x, 50), line)
    canvas.paste(line, (top_x, 1030 - line.height // 2), line)

    # Главный круглый аватар.
    main_avatar = circle_crop(avatar_path, MAIN_AVATAR_RADIUS, username)
    paste_center(canvas, main_avatar, MAIN_AVATAR_CENTER)

    handwritten_name = load_font("handwritten.ttf", 58)
    handwritten_date = load_font("handwritten.ttf", 44)
    draw_centered_text(draw, username, MAIN_AVATAR_CENTER[0], 780, handwritten_name)
    draw_centered_text(draw, date_text, MAIN_AVATAR_CENTER[0], 850, handwritten_date)

    # Цитата справа с динамическим размером шрифта.
    quote_font, quote_lines, (quote_w, quote_h) = fit_quote_font(
        quote_text,
        "main.ttf",
        QUOTE_BOX,
        draw,
        max_size=100,
        min_size=24,
        line_spacing=14,
    )
    quote_rendered = "\n".join(quote_lines)
    left, top, right, bottom = QUOTE_BOX
    quote_x = left + (right - left) // 2
    quote_y = top + (bottom - top - quote_h) // 2

    draw.multiline_text(
        (quote_x, quote_y),
        quote_rendered,
        font=quote_font,
        fill="white",
        spacing=14,
        align="center",
        anchor="ma",
    )

    # Большие кавычки относительно получившегося блока текста.
    quote_mark_font = load_font("main.ttf", 130)
    draw.text((left - 70, quote_y - 80), "“", font=quote_mark_font, fill="white")
    draw.text((right - 20, quote_y + quote_h - 10), "”", font=quote_mark_font, fill="white")

    # Правый нижний угол: системная строка + маленькая круглая ава.
    small_avatar = circle_crop(small_avatar_path, SMALL_AVATAR_RADIUS, username)
    paste_center(canvas, small_avatar, SMALL_AVATAR_CENTER)

    system_font = load_font("main.ttf", 42)
    sys_bbox = draw.textbbox((0, 0), system_text, font=system_font)
    sys_w = sys_bbox[2] - sys_bbox[0]
    sys_x = SMALL_AVATAR_CENTER[0] - SMALL_AVATAR_RADIUS - 45 - sys_w
    sys_y = SMALL_AVATAR_CENTER[1] - 25
    draw.text((sys_x, sys_y), system_text, font=system_font, fill="white")

    out_path = BASE_DIR / output
    canvas.convert("RGB").save(out_path, quality=95)
    return out_path


if __name__ == "__main__":
    result = generate_card(
        username="Carrie Chaser",
        date_text="05 June 2026, 15:39",
        quote_text="я матвей я согласен",
        output="profile_quote.png",
    )
    print(f"Saved: {result}")
