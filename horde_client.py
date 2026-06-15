"""Stable Horde — бесплатная распределённая генерация картинок."""

from __future__ import annotations

import base64
import logging
import time

import requests

logger = logging.getLogger(__name__)

HORDE_BASE = "https://aihorde.net/api/v2"
ANONYMOUS_API_KEY = "0000000000"
DEFAULT_TIMEOUT_SEC = 180
POLL_INTERVAL_SEC = 3


def generate_image(
    prompt: str,
    *,
    api_key: str = "",
    width: int = 512,
    height: int = 512,
    steps: int = 25,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> bytes:
    query = prompt.strip()
    if not query:
        raise ValueError("Пустой промпт для Stable Horde")

    headers = {"apikey": api_key.strip() or ANONYMOUS_API_KEY}
    payload = {
        "prompt": query,
        "params": {
            "steps": steps,
            "width": width,
            "height": height,
            "n": 1,
        },
        "nsfw": True,
        "censor_nsfw": False,
        "trusted_workers": False,
        "slow_workers": True,
        "models": [],
    }
    response = requests.post(
        f"{HORDE_BASE}/generate/async",
        headers=headers,
        json=payload,
        timeout=45,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"Horde async {response.status_code}: {(response.text or '')[:300]}")
    job_id = response.json().get("id")
    if not job_id:
        raise RuntimeError("Stable Horde не вернул id задачи")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        check = requests.get(
            f"{HORDE_BASE}/generate/check/{job_id}",
            headers=headers,
            timeout=30,
        )
        if check.status_code >= 400:
            raise RuntimeError(f"Horde check {check.status_code}: {(check.text or '')[:300]}")
        if check.json().get("done"):
            break
        time.sleep(POLL_INTERVAL_SEC)
    else:
        raise TimeoutError("Stable Horde: таймаут ожидания картинки")

    status = requests.get(
        f"{HORDE_BASE}/generate/status/{job_id}",
        headers=headers,
        timeout=45,
    )
    if status.status_code >= 400:
        raise RuntimeError(f"Horde status {status.status_code}: {(status.text or '')[:300]}")
    generations = status.json().get("generations") or []
    if not generations:
        raise RuntimeError("Stable Horde: пустой результат")
    image_b64 = generations[0].get("img")
    if not image_b64:
        raise RuntimeError("Stable Horde: нет img в ответе")
    logger.info("Stable Horde: картинка готова")
    return base64.b64decode(image_b64)
