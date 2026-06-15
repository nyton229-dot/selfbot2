"""FusionBrain / Kandinsky — генерация картинок."""

from __future__ import annotations

import base64
import json
import logging
import time

import requests

logger = logging.getLogger(__name__)

FUSIONBRAIN_URL = "https://api-key.fusionbrain.ai/"
DEFAULT_TIMEOUT_SEC = 120
POLL_INTERVAL_SEC = 2


def _headers(api_key: str, secret_key: str) -> dict[str, str]:
    return {
        "X-Key": f"Key {api_key}",
        "X-Secret": f"Secret {secret_key}",
    }


def _model_id(api_key: str, secret_key: str) -> str:
    response = requests.get(
        f"{FUSIONBRAIN_URL}key/api/v1/models",
        headers=_headers(api_key, secret_key),
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"FusionBrain models {response.status_code}: {(response.text or '')[:300]}")
    payload = response.json()
    if isinstance(payload, list):
        if not payload:
            raise RuntimeError("FusionBrain: нет доступных моделей")
        return str(payload[0]["id"])
    if isinstance(payload, dict) and payload.get("id") is not None:
        return str(payload["id"])
    raise RuntimeError("FusionBrain: неожиданный ответ models")


def generate_image(
    prompt: str,
    *,
    api_key: str,
    secret_key: str,
    width: int = 1024,
    height: int = 1024,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> bytes:
    query = prompt.strip()
    if not query:
        raise ValueError("Пустой промпт для Kandinsky")

    model_id = _model_id(api_key, secret_key)
    params = {
        "type": "GENERATE",
        "numImages": 1,
        "width": width,
        "height": height,
        "generateParams": {"query": query},
    }
    response = requests.post(
        f"{FUSIONBRAIN_URL}key/api/v1/text2image/run",
        headers=_headers(api_key, secret_key),
        files={
            "model_id": (None, model_id),
            "params": (None, json.dumps(params), "application/json"),
        },
        timeout=60,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"FusionBrain run {response.status_code}: {(response.text or '')[:300]}")
    job_id = response.json().get("uuid")
    if not job_id:
        raise RuntimeError("FusionBrain не вернул uuid задачи")

    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        status_response = requests.get(
            f"{FUSIONBRAIN_URL}key/api/v1/text2image/status/{job_id}",
            headers=_headers(api_key, secret_key),
            timeout=30,
        )
        if status_response.status_code >= 400:
            raise RuntimeError(
                f"FusionBrain status {status_response.status_code}: {(status_response.text or '')[:300]}"
            )
        status = status_response.json()
        state = (status.get("status") or "").upper()
        if state == "DONE":
            images = status.get("images") or []
            if not images:
                raise RuntimeError("FusionBrain: пустой результат")
            logger.info("FusionBrain: картинка готова (%d симв. b64)", len(images[0]))
            return base64.b64decode(images[0])
        if state in ("FAIL", "FAILED", "ERROR"):
            raise RuntimeError(status.get("statusDescription") or "FusionBrain: генерация провалилась")
        time.sleep(POLL_INTERVAL_SEC)

    raise TimeoutError("FusionBrain: таймаут ожидания картинки")
