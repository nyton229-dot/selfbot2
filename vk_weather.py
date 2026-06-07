"""Команда /погода — AQI и погода через WAQI (aqicn.org)."""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import requests

logger = logging.getLogger(__name__)

WEATHER_COMMAND = "/погода"
WEATHER_COMMAND_ALT = "/weather"
WAQI_API_BASE = "https://api.waqi.info"
WAQI_TIMEOUT = (4, 8)

_CITY_ALIASES: dict[str, str] = {
    "москва": "moscow",
    "moskva": "moscow",
    "санкт-петербург": "saint-petersburg",
    "спб": "saint-petersburg",
    "питер": "saint-petersburg",
    "saint petersburg": "saint-petersburg",
    "новосибирск": "novosibirsk",
    "екатеринбург": "yekaterinburg",
    "екб": "yekaterinburg",
    "казань": "kazan",
    "нижний новгород": "nizhny-novgorod",
    "самара": "samara",
    "омск": "omsk",
    "челябинск": "chelyabinsk",
    "красноярск": "krasnoyarsk",
    "воронеж": "voronezh",
    "пермь": "perm",
    "волгоград": "volgograd",
    "краснодар": "krasnodar",
    "минск": "minsk",
    "киев": "kyiv",
    "kyiv": "kyiv",
    "алматы": "almaty",
    "астана": "astana",
}

_AQI_LABELS = (
    (0, 50, "Хорошо"),
    (51, 100, "Умеренно"),
    (101, 150, "Неблагоприятно для чувствительных"),
    (151, 200, "Нездорово"),
    (201, 300, "Очень нездорово"),
    (301, 10_000, "Опасно"),
)

_IAQI_NAMES = {
    "pm25": "PM2.5",
    "pm10": "PM10",
    "o3": "O₃",
    "no2": "NO₂",
    "so2": "SO₂",
    "co": "CO",
    "t": "Температура",
    "h": "Влажность",
    "p": "Давление",
    "w": "Ветер",
}

_session = requests.Session()


def is_weather_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in (WEATHER_COMMAND, WEATHER_COMMAND_ALT):
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def parse_city_name(text: str) -> str | None:
    stripped = text.strip()
    lower = stripped.casefold()
    for prefix in (WEATHER_COMMAND, WEATHER_COMMAND_ALT):
        cmd = prefix.casefold()
        if lower == cmd:
            return None
        if lower.startswith(cmd + " "):
            city = stripped[len(prefix) :].strip()
            return city or None
    return None


def _resolve_city_query(city: str) -> str:
    key = city.strip().casefold().replace("ё", "е")
    return _CITY_ALIASES.get(key, city.strip())


def _aqi_label(aqi: int) -> str:
    for low, high, label in _AQI_LABELS:
        if low <= aqi <= high:
            return label
    return "—"


def _iaqi_value(iaqi: dict[str, Any], key: str) -> float | None:
    block = iaqi.get(key)
    if not isinstance(block, dict):
        return None
    value = block.get("v")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_feed(data: dict[str, Any]) -> str:
    city_info = data.get("city") or {}
    city_name = (city_info.get("name") or "Город").strip()
    try:
        aqi = int(data.get("aqi", -1))
    except (TypeError, ValueError):
        aqi = -1
    iaqi = data.get("iaqi") if isinstance(data.get("iaqi"), dict) else {}
    lines = [city_name]

    if aqi >= 0:
        lines.append(f"AQI: {aqi} ({_aqi_label(aqi)})")

    pollutants: list[str] = []
    for key in ("pm25", "pm10", "o3", "no2"):
        value = _iaqi_value(iaqi, key)
        if value is not None:
            name = _IAQI_NAMES.get(key, key)
            pollutants.append(f"{name}: {value:g}")
    if pollutants:
        lines.append(" · ".join(pollutants))

    weather_parts: list[str] = []
    temp = _iaqi_value(iaqi, "t")
    if temp is not None:
        weather_parts.append(f"{temp:g}°C")
    humidity = _iaqi_value(iaqi, "h")
    if humidity is not None:
        weather_parts.append(f"влажность {humidity:g}%")
    wind = _iaqi_value(iaqi, "w")
    if wind is not None:
        weather_parts.append(f"ветер {wind:g} м/с")
    pressure = _iaqi_value(iaqi, "p")
    if pressure is not None:
        weather_parts.append(f"давление {pressure:g} hPa")
    if weather_parts:
        lines.append("Погода: " + ", ".join(weather_parts))

    lines.append("Данные: aqicn.org")
    return "\n".join(lines)


def _api_get(path: str, token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    query = {"token": token}
    if params:
        query.update(params)
    response = _session.get(f"{WAQI_API_BASE}{path}", params=query, timeout=WAQI_TIMEOUT)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("WAQI вернул неожиданный ответ")
    return payload


def _feed(path_segment: str, token: str) -> dict[str, Any]:
    encoded = urllib.parse.quote(path_segment.strip(), safe="")
    payload = _api_get(f"/feed/{encoded}/", token)
    if payload.get("status") == "ok" and isinstance(payload.get("data"), dict):
        return payload["data"]
    raise LookupError("нет данных")


def _search_uid(city: str, token: str) -> str:
    payload = _api_get("/search/", token, {"keyword": city.strip()})
    if payload.get("status") != "ok":
        raise LookupError(f"Город «{city}» не найден")
    items = payload.get("data")
    if not isinstance(items, list) or not items:
        raise LookupError(f"Город «{city}» не найден")
    uid = items[0].get("uid")
    if not uid:
        raise LookupError(f"Город «{city}» не найден")
    return str(uid)


def fetch_weather_report(city: str, token: str) -> str:
    if not token.strip():
        raise RuntimeError(
            "Не задан WAQI_TOKEN. Получи ключ на https://aqicn.org/data-platform/token/ "
            "и добавь в .env"
        )

    query = _resolve_city_query(city)

    try:
        data = _feed(query, token)
    except (LookupError, requests.RequestException):
        logger.debug("Feed %r не сработал, search+feed", query)
        try:
            uid = _search_uid(city, token)
            data = _feed(uid, token)
        except requests.RequestException as exc:
            raise exc
        except LookupError as exc:
            raise LookupError(f"Город «{city}» не найден") from exc

    return _format_feed(data)
