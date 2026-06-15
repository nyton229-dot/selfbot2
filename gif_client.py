"""Tenor, Giphy и встроенный fallback — поиск GIF."""

from __future__ import annotations

import logging
import random
import re

import requests

logger = logging.getLogger(__name__)

_CLIENT_KEY = "lpbot"


def _giphy_cdn(gif_id: str) -> str:
    return f"https://i.giphy.com/media/{gif_id}/giphy.gif"


# Прямые CDN-ссылки (API Giphy не нужен). Проверены на доступность без ключа.
_BUILTIN_POOLS: dict[str, tuple[str, ...]] = {
    "default": (
        "l3q2K5jinAlChoCLS",
        "3o7aCTPPm4OHfRLSH6",
        "3o7abKhOpu0NwenH3O",
        "l0MYt5jPR6QX5pnqM",
        "g9582DNuQppxC",
    ),
    "facepalm": ("l3q2K5jinAlChoCLS", "3o7aCTPPm4OHfRLSH6"),
    "wow": ("3o7aCTPPm4OHfRLSH6", "l3q2K5jinAlChoCLS"),
    "yes": ("3o7abKhOpu0NwenH3O", "g9582DNuQppxC"),
    "angry": ("l0MYt5jPR6QX5pnqM", "3o7aCTPPm4OHfRLSH6"),
    "clap": ("g9582DNuQppxC", "3o7abKhOpu0NwenH3O"),
    "laugh": ("3o7aCTPPm4OHfRLSH6", "g9582DNuQppxC"),
    "no": ("l3q2K5jinAlChoCLS", "l0MYt5jPR6QX5pnqM"),
}

_KEYWORD_TO_POOL: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("facepalm", ("facepalm", "фейспалм", "reaction", "meme", "реакц")),
    ("laugh", ("смех", "laugh", "lol", "ржу", "хах", "ахах", "funny", "смешн")),
    ("yes", ("yes", "да", "ок", "окей", "ага", "конечно", "approve", "thumbs up")),
    ("no", ("no", "нет", "неа", "отказ", "nope")),
    ("angry", ("angry", "злой", "злость", "бесит", "rage", "mad", "furious")),
    ("clap", ("clap", "браво", "аплод", "молодец")),
    ("wow", ("wow", "вау", "ого", "shock", "удив")),
)


def _pick_tenor_url(payload: dict) -> str | None:
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    media = results[0].get("media_formats") or {}
    for key in ("gif", "mediumgif", "tinygif", "nanogif"):
        item = media.get(key)
        if isinstance(item, dict) and item.get("url"):
            return str(item["url"])
    return None


def _pick_klipy_url(payload: dict) -> str | None:
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    item = data[0]
    if not isinstance(item, dict):
        return None
    for key in ("gif", "mediumgif", "tinygif", "nanogif"):
        fmt = item.get(key)
        if isinstance(fmt, dict) and fmt.get("url"):
            return str(fmt["url"])
    file_obj = item.get("file") or {}
    if isinstance(file_obj, dict):
        hd = file_obj.get("hd") or file_obj.get("md") or file_obj.get("sm")
        if isinstance(hd, dict) and hd.get("gif"):
            return str(hd["gif"])
    return None


def _normalize_query(query: str) -> str:
    cleaned = re.sub(r"\s+", " ", query.strip().casefold())
    return cleaned or "reaction meme"


def _builtin_pool_for_query(query: str) -> str:
    for pool_name, keywords in _KEYWORD_TO_POOL:
        if any(keyword in query for keyword in keywords):
            return pool_name
    return "default"


def search_builtin(query: str) -> str:
    normalized = _normalize_query(query)
    pool_name = _builtin_pool_for_query(normalized)
    gif_id = random.choice(_BUILTIN_POOLS[pool_name])
    url = _giphy_cdn(gif_id)
    logger.info("Builtin GIF pool=%s query=%r", pool_name, query[:80])
    return url


def search_tenor(query: str, *, api_key: str, limit: int = 1) -> str:
    response = requests.get(
        "https://tenor.googleapis.com/v2/search",
        params={
            "q": query,
            "key": api_key,
            "client_key": _CLIENT_KEY,
            "limit": limit,
            "media_filter": "gif",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Tenor {response.status_code}: {(response.text or '')[:300]}")
    url = _pick_tenor_url(response.json())
    if not url:
        raise RuntimeError(f"Tenor: ничего не нашёл по «{query}»")
    logger.info("Tenor GIF: %s", query[:80])
    return url


def search_giphy(query: str, *, api_key: str, limit: int = 1) -> str:
    response = requests.get(
        "https://api.giphy.com/v1/gifs/search",
        params={
            "api_key": api_key,
            "q": query,
            "limit": limit,
            "rating": "r",
            "lang": "ru",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Giphy {response.status_code}: {(response.text or '')[:300]}")
    data = response.json().get("data") or []
    if not data:
        raise RuntimeError(f"Giphy: ничего не нашёл по «{query}»")
    images = data[0].get("images") or {}
    for key in ("downsized", "fixed_height", "original"):
        item = images.get(key)
        if isinstance(item, dict) and item.get("url"):
            logger.info("Giphy GIF: %s", query[:80])
            return str(item["url"])
    raise RuntimeError(f"Giphy: нет url для «{query}»")


def search_klipy(query: str, *, api_key: str, limit: int = 1) -> str:
    response = requests.get(
        f"https://api.klipy.com/api/v1/{api_key}/gifs/search",
        params={"q": query, "limit": limit},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Klipy {response.status_code}: {(response.text or '')[:300]}")
    url = _pick_klipy_url(response.json())
    if not url:
        raise RuntimeError(f"Klipy: ничего не нашёл по «{query}»")
    logger.info("Klipy GIF: %s", query[:80])
    return url


def search_gif(
    query: str,
    *,
    tenor_api_key: str = "",
    giphy_api_key: str = "",
    klipy_api_key: str = "",
    allow_builtin: bool = True,
) -> tuple[str, str]:
    cleaned = query.strip() or "reaction meme"
    errors: list[str] = []

    if tenor_api_key.strip():
        try:
            return search_tenor(cleaned, api_key=tenor_api_key.strip()), "tenor"
        except RuntimeError as exc:
            errors.append(str(exc))

    if giphy_api_key.strip():
        try:
            return search_giphy(cleaned, api_key=giphy_api_key.strip()), "giphy"
        except RuntimeError as exc:
            errors.append(str(exc))

    if klipy_api_key.strip():
        try:
            return search_klipy(cleaned, api_key=klipy_api_key.strip()), "klipy"
        except RuntimeError as exc:
            errors.append(str(exc))

    if allow_builtin:
        return search_builtin(cleaned), "builtin"

    hint = (
        "Добавь TENOR_API_KEY (https://developers.google.com/tenor) "
        "или GIPHY_API_KEY (https://developers.giphy.com/) в .env"
    )
    if errors:
        raise RuntimeError(f"{'; '.join(errors[-2:])}. {hint}")
    raise RuntimeError(hint)
