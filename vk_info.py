"""Команда /инфо — какой ИИ использует бот."""

from __future__ import annotations

INFO_COMMAND = "/инфо"
INFO_COMMAND_ALT = "/info"


def is_info_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for command in (INFO_COMMAND, INFO_COMMAND_ALT):
        cmd = command.casefold()
        if normalized == cmd or normalized.startswith(f"{cmd} "):
            return True
    return False


def format_ai_info(
    *,
    provider: str,
    text_model: str,
    vision_model: str,
    whisper_model: str,
) -> str:
    provider_label = provider.strip().casefold()
    if provider_label == "openrouter":
        provider_name = "OpenRouter"
    elif provider_label == "bothub":
        provider_name = "BotHub"
    else:
        provider_name = provider.strip() or "неизвестно"

    text = text_model.strip() or "не задана"
    vision = vision_model.strip() or "не задана"
    whisper = whisper_model.strip() or "не задана"

    lines = [
        "ИИ в этом боте:",
        f"• Провайдер: {provider_name}",
        f"• Текст (сообщения без медиа): {text}",
        f"• Фото и видео (кадры): {vision}",
        f"• Речь из видео (Whisper): {whisper}",
    ]
    if text.casefold() != vision.casefold():
        lines.append("Текст и медиа — разные модели (текст дешевле).")
    lines.append("Триггер: «артем …»")
    lines.append("Кратко: reply на сообщение + «артем кратко»")
    lines.append("Озвучка: «артем озвучь …» или «артем … озвучь»")
    lines.append("Ответить человеку голосом: reply на его сообщение + «артем ответь ему»")
    lines.append("Спорить голосом: reply на сообщение + «артем спорь»")
    lines.append("Голос озвучки (владелец): /голос или /голос список")
    lines.append("Сменить модель (владелец): /ии или /ии список")
    return "\n".join(lines)
