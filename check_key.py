"""Проверка, что API-ключ принадлежит BotHub (не публикуйте ключ в чатах)."""
import json
import sys
import urllib.error
import urllib.request

BASE_URL = "https://bothub.chat/api/v2/openai/v1"


def decode_jwt_payload(token: str) -> dict:
    import base64

    parts = token.strip().split(".")
    if len(parts) != 3:
        raise ValueError("Не похоже на JWT (ожидается 3 части через точку)")

    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def probe_bothub(token: str) -> import tye vkai   req = urllib.request.Request(
        f"{BASE_URL}/models",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read(500).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(500).decode("utf-8", "replace")


def main() -> None:
    if len(sys.argv) != 2:
        print("Использование: python check_key.py YOUR_API_KEY")
        sys.exit(1)

    token = sys.argv[1]

    print("=== Анализ формата ===")
    if token.startswith("eyJ"):
        print("Формат: JWT (eyJ...)")
        try:
            payload = decode_jwt_payload(token)
            print("Payload:", json.dumps(payload, ensure_ascii=False, indent=2))
            if payload.get("isDeveloper") is True:
                print("Признак: isDeveloper=true → типичный ключ BotHub (раздел «Для разработчиков»)")
        except Exception as exc:
            print("Не удалось декодировать payload:", exc)
    elif token.startswith("sk-"):
        print("Формат: sk-... → скорее OpenAI или OpenAI-совместимый прокси")
    else:
        print("Формат: неизвестный, проверьте документацию сервиса, где брали ключ")

    print("\n=== Проверка BotHub API ===")
    status, body = probe_bothub(token)
    print(f"HTTP {status}")
    if status == 200:
        print("Вывод: ключ работает с BotHub (https://bothub.chat/api/v2/openai/v1)")
    elif status == 401:
        print("Вывод: BotHub отклонил ключ (неверный/просроченный или это не BotHub)")
    else:
        print("Ответ:", body[:300])


if __name__ == "__main__":
    main()
