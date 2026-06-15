"""
VK User LongPoll бот для личного аккаунта + BotHub (OpenAI-compatible API).

Работает только в беседах (групповых чатах), не в личных сообщениях.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests
import vk_api
from vk_api.exceptions import ApiError
from vk_api.longpoll import VkEventType, VkLongPoll
from vk_api.utils import get_random_id

from ai_models import (
    format_invalid_model,
    format_models_list,
    format_models_status,
    init_model_store,
    is_ii_command,
    parse_ii_command,
)
from ai_access import (
    AccessCommand,
    AiAccessStore,
    init_access_store,
    is_dov_list_command,
    mention_user_id_from_text,
    parse_access_command,
)
from ai_client import (
    AiClient,
    format_bot_message,
    sanitize_media_context_for_owner,
    wants_photo_compare,
)
from config import Settings, data_dir, load_settings
from fusionbrain_client import generate_image as generate_kandinsky_image
from gif_client import search_gif
from horde_client import generate_image as generate_horde_image
from instance_lock import acquire_instance_lock, release_instance_lock
from meme_client import build_meme
from quote_client import build_quote_image
from video_transcribe import should_transcribe_video, transcribe_videos_in_message
from vk_avatar import (
    fetch_user_avatar,
    is_ava_command,
    parse_profile_reference,
    reply_author_id,
    resolve_user_id,
    send_avatar_reply,
)
from vk_delete import FAKE_DELETE_MESSAGES, is_delete_command
from vk_info import format_ai_info, is_info_command
from vk_weather import fetch_weather_report, is_weather_command, parse_city_name
from vk_reply import (
    ReplyToUserContext,
    format_user_mention,
    get_reply_message,
    mention_label,
    parse_reply_to_user_command,
    reply_message_conversation_id,
    reply_message_text,
)
from vk_media_send import send_gif_url, send_image_bytes
from vk_text_cmds import (
    build_image_prompt,
    build_kratko_prompt,
    build_meme_caption,
    build_narisuy_caption,
    build_spor_extra_instruction,
    parse_gif_command,
    parse_kratko_command,
    parse_meme_command,
    parse_narisuy_command,
    parse_quote_command,
    parse_spor_command,
    strip_tts_keyword,
)
from vk_media import (
    count_photos,
    load_message_images,
    media_in_current_message,
    media_is_self_upload,
    message_has_media,
    message_has_photos,
    message_has_videos,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Команда и префикс «ручного перехвата» диалога
STOP_COMMAND = "!стоп"
MANUAL_PREFIX = "."
# ИИ отвечает только если сообщение начинается с этого префикса (без учёта регистра)
TRIGGER_PREFIX = "артем"


def should_ignore_message(text: str) -> bool:
    """Предохранитель: не отвечаем на команды ручного управления."""
    normalized = text.strip()
    if not normalized:
        return True
    if normalized.lower() == STOP_COMMAND:
        return True
    if normalized.startswith(MANUAL_PREFIX):
        return True
    return False


def has_trigger(text: str) -> bool:
    return text.strip().casefold().startswith(TRIGGER_PREFIX.casefold())


def resolve_prompt(text: str, has_media: bool, photo_count: int = 0) -> str | None:
    """Текст после «артем» или дефолт для сообщений с фото/видео."""
    if not has_trigger(text):
        return None

    normalized = text.strip()
    rest = normalized[len(TRIGGER_PREFIX) :].lstrip(" ,:-—").strip()
    if rest:
        return rest
    if photo_count >= 2:
        return "сравни оба фото"
    if photo_count >= 1:
        return "оцени картинку"
    if has_media:
        return (
            "Грубо и зло скажи что на фото или видео — без сухого «на фотке мужик держит». "
            "Сразу осуди кадр, унизь, по-кавказски."
        )
    return None


def _message_data(event: Any) -> dict[str, Any] | None:
    message_data = getattr(event, "message_data", None)
    return message_data if isinstance(message_data, dict) else None


def event_unix_time(event: Any) -> float | None:
    message_data = _message_data(event)
    if not message_data:
        return None
    raw_date = message_data.get("date")
    if raw_date is None:
        return None
    try:
        return float(raw_date)
    except (TypeError, ValueError):
        return None


def is_fresh_event(event: Any, listen_started_at: float, grace_sec: int) -> bool:
    message_time = event_unix_time(event)
    if message_time is None:
        return False
    return message_time >= listen_started_at - grace_sec


def _outgoing_command_allowed(event: Any, outgoing_only: bool) -> bool:
    if not outgoing_only:
        return True
    return bool(event.from_me)


def event_has_media(event: Any) -> bool:
    return message_has_media(_message_data(event))


def event_has_videos(event: Any) -> bool:
    return message_has_videos(_message_data(event))


def is_chat_message_with_text(event: Any) -> bool:
    """Только беседы (групповые чаты), не личные диалоги."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    text = getattr(event, "text", None)
    if not text and not event_has_media(event):
        return False
    if getattr(event, "user_id", 0) < 0:
        return False
    return True


def should_queue_weather_event(event: Any) -> bool:
    """Команда /погода — AQI и погода по городу."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    text = (event.text or "").strip()
    if not is_weather_command(text):
        return False
    if event.from_me:
        return True
    return bool(event.to_me and not event.from_me)


def should_queue_ava_event(event: Any) -> bool:
    """Команда /ава — без ИИ, в беседах."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    text = (event.text or "").strip()
    if not is_ava_command(text):
        return False
    if event.from_me:
        return True
    return bool(event.to_me and not event.from_me)



def should_queue_info_event(event: Any) -> bool:
    """Команда /инфо — сведения об используемом ИИ."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    text = (event.text or "").strip()
    if not is_info_command(text):
        return False
    if event.from_me:
        return True
    return bool(event.to_me and not event.from_me)


def should_queue_delete_event(event: Any) -> bool:
    """Фейк /удалить — только от владельца."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    if not event.from_me:
        return False
    return is_delete_command((event.text or "").strip())


def should_queue_ii_event(event: Any) -> bool:
    """Команда /ии — смена моделей, только от владельца."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    if not event.from_me:
        return False
    return is_ii_command((event.text or "").strip())



def should_queue_dov_list_event(event: Any) -> bool:
    """Команда /дов — список участников с доступом к ИИ."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    text = (event.text or "").strip()
    if not is_dov_list_command(text):
        return False
    if event.from_me:
        return True
    return bool(event.to_me and not event.from_me)


def should_queue_access_event(event: Any) -> bool:
    """Команды /+дов и /-дов — только от владельца."""
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return False
    if not event.from_me:
        return False
    text = (event.text or "").strip()
    return parse_access_command(text) is not None


def should_queue_ai_event(event: Any, access_store: AiAccessStore) -> bool:
    """
    В очередь попадают сообщения из бесед:
    - исходящие с префиксом «артем» (владелец);
    - входящие с «артем» только у тех, кому выдан доступ.
    """
    if not is_chat_message_with_text(event):
        return False

    text = (event.text or "").strip()
    has_media = event_has_media(event)
    photo_count = count_photos(_message_data(event))
    if resolve_prompt(text, has_media, photo_count) is None:
        return False

    if event.from_me and has_trigger(text):
        return True

    if event.to_me and not event.from_me:
        return access_store.has_access(event.peer_id, event.user_id)

    return False


def _handle_longpoll_event(
    event: Any,
    queue: asyncio.Queue[Any],
    loop: asyncio.AbstractEventLoop,
    access_store: AiAccessStore,
    account_id: str,
) -> None:
    if event.type != VkEventType.MESSAGE_NEW or not event.from_chat:
        return

    text_preview = (event.text or "")[:80]
    photos = message_has_photos(_message_data(event))
    videos = event_has_videos(event)
    if text_preview or photos or videos:
        logger.info(
            "Событие беседа [акк %s]: chat_id=%s from_me=%s to_me=%s user_id=%s photos=%s videos=%s text=%r",
            account_id,
            event.chat_id,
            event.from_me,
            event.to_me,
            event.user_id,
            photos,
            videos,
            text_preview,
        )

    kind: str | None = None
    if should_queue_weather_event(event):
        kind = "weather"
    elif should_queue_ava_event(event):
        kind = "ava"
    elif should_queue_info_event(event):
        kind = "info"
    elif should_queue_ii_event(event):
        kind = "ii"
    elif should_queue_delete_event(event):
        kind = "delete"
    elif should_queue_dov_list_event(event):
        kind = "dov"
    elif should_queue_access_event(event):
        kind = "access"
    elif should_queue_ai_event(event, access_store):
        kind = "ai"
    if kind:
        logger.info("В очередь [акк %s]: %s chat_id=%s", account_id, kind, event.chat_id)
        asyncio.run_coroutine_threadsafe(queue.put((kind, event)), loop)


def vk_listener_thread(
    vk_session: Any,
    preload_messages: bool,
    queue: asyncio.Queue[Any],
    loop: asyncio.AbstractEventLoop,
    access_store: AiAccessStore,
    account_id: str,
) -> None:
    """LongPoll в отдельном потоке с переподключением без перезапуска процесса."""
    backoff_sec = 5
    max_backoff_sec = 120

    while True:
        longpoll = VkLongPoll(vk_session, preload_messages=preload_messages)
        logger.info("LongPoll [акк %s] запущен, ожидаю сообщения в беседах...", account_id)
        try:
            for event in longpoll.listen():
                _handle_longpoll_event(event, queue, loop, access_store, account_id)
            backoff_sec = 5
        except ApiError as error:
            if error.code in (5, 18):
                logger.error(
                    "VK отклонил доступ (code=%s). Проверь VK_USER_TOKEN, не запускай вторую копию бота.",
                    error.code,
                )
                return
            logger.warning(
                "LongPoll VK API code=%s, переподключение через %d сек: %s",
                error.code,
                backoff_sec,
                error,
            )
            time.sleep(backoff_sec)
            backoff_sec = min(backoff_sec * 2, max_backoff_sec)
        except Exception:
            logger.exception(
                "LongPoll упал, переподключение через %d сек (не запускай вторую копию бота)",
                backoff_sec,
            )
            time.sleep(backoff_sec)
            backoff_sec = min(backoff_sec * 2, max_backoff_sec)


def _account_store_path(base: str, account_id: str, multi_account: bool) -> Path:
    if not multi_account or account_id == "1":
        return data_dir() / f"{base}.json"
    return data_dir() / f"{base}_{account_id}.json"


class VkDmBot:
    def __init__(
        self,
        settings: Settings,
        *,
        vk_token: str,
        account_id: str,
        multi_account: bool,
    ) -> None:
        self._settings = settings
        self._account_id = account_id
        self._models = init_model_store(
            _account_store_path("ai_models", account_id, multi_account),
            settings.ai_model,
            settings.ai_vision_model,
        )
        self._ai = AiClient(settings, self._models)

        self._vk_session = vk_api.VkApi(token=vk_token)
        self._vk = self._vk_session.get_api()
        self._longpoll_preload = settings.longpoll_preload
        self._owner_labels: set[str] = {"артем"}
        self._owner_names: tuple[str, ...] = ()
        self._my_user_id = self._init_owner_account()
        self._access = init_access_store(
            _account_store_path("ai_access", account_id, multi_account),
            self._my_user_id,
        )
        self._user_names: dict[int, str] = {}
        logger.info(
            "Акк %s: AI provider=%s text_model=%s vision_model=%s",
            account_id,
            settings.ai_provider,
            self._models.text_model,
            self._models.vision_model,
        )

    def _init_owner_account(self) -> int:
        """ID аккаунта и все его подписи в истории — чтобы не унижать владельца."""
        user = self._vk.users.get()[0]
        user_id = int(user["id"])
        first = (user.get("first_name") or "").strip()
        last = (user.get("last_name") or "").strip()
        names: list[str] = []
        for label in ("Артем", first, last, f"{first} {last}".strip(), str(user_id)):
            if label:
                names.append(label)
                self._owner_labels.add(label.casefold())
        self._owner_names = tuple(dict.fromkeys(names))
        logger.info(
            "Авторизован как user_id=%s (%s %s)",
            user_id,
            user.get("first_name"),
            user.get("last_name"),
        )
        return user_id

    def _is_owner_label(self, label: str) -> bool:
        return label.strip().casefold() in self._owner_labels

    @staticmethod
    def _conversation_message_id(event: Any) -> int | None:
        """ID сообщения внутри беседы — нужен для reply."""
        message_data = getattr(event, "message_data", None)
        if isinstance(message_data, dict):
            cmid = message_data.get("conversation_message_id")
            if cmid is not None:
                return int(cmid)
        return None

    def _resolve_roast_target(self, event: Any, peer_id: int) -> str:
        """Кого унижать, когда команду запускает владелец аккаунта."""
        reply_user_id = reply_author_id(event)
        if reply_user_id and reply_user_id > 0 and reply_user_id != self._my_user_id:
            return self._resolve_roast_target_from_id(reply_user_id)

        member = self._pick_chat_member_name(peer_id)
        if member:
            return member

        return "участника беседы"

    def _resolve_roast_target_from_id(self, user_id: int) -> str:
        if user_id in self._user_names:
            return self._user_names[user_id]
        try:
            users = self._vk.users.get(user_ids=user_id)
            if users:
                user = users[0]
                name = f"{user.get('first_name', '')} {user.get('last_name', '')}".strip()
                if name:
                    self._user_names[user_id] = name
                    return name
        except ApiError:
            logger.debug("Не удалось получить имя roast target user_id=%s", user_id)
        return f"участника (id{user_id})"

    def _pick_chat_member_name(self, peer_id: int) -> str | None:
        """Участник беседы из VK API."""
        try:
            response = self._vk.messages.getConversationMembers(peer_id=peer_id, extended=1)
        except ApiError:
            logger.debug("Не удалось получить участников peer_id=%s", peer_id)
            return None

        for item in response.get("items", []):
            member_id = int(item.get("member_id") or item.get("id") or 0)
            if member_id > 0 and member_id != self._my_user_id:
                name = self._resolve_roast_target_from_id(member_id)
                if not self._is_owner_label(name):
                    return name

        for profile in response.get("profiles", []):
            user_id = int(profile["id"])
            if user_id == self._my_user_id:
                continue
            name = f"{profile.get('first_name', '')} {profile.get('last_name', '')}".strip()
            if name and not self._is_owner_label(name):
                return name
        return None

    def _build_reply_to_user_context(
        self,
        event: Any,
        message_data: dict[str, Any] | None,
        extra_instruction: str,
    ) -> ReplyToUserContext | None:
        reply_msg = get_reply_message(message_data)
        if not reply_msg:
            return None

        target_id = reply_author_id(event)
        if not target_id or target_id <= 0:
            from_id = reply_msg.get("from_id")
            if from_id is not None:
                target_id = int(from_id)
        if not target_id or target_id <= 0:
            return None

        target_name = self._resolve_roast_target_from_id(target_id)
        label = mention_label(target_name, target_id)
        mention = format_user_mention(target_id, label)
        replied_text = reply_message_text(reply_msg)
        reply_cmid = reply_message_conversation_id(reply_msg)
        return ReplyToUserContext(
            target_user_id=target_id,
            target_name=target_name,
            target_mention=mention,
            replied_text=replied_text,
            reply_cmid=reply_cmid,
            extra_instruction=extra_instruction,
        )

    def reply(
        self,
        peer_id: int,
        text: str,
        event: Any,
        *,
        reply_to_cmid: int | None = None,
    ) -> None:
        """Отправляет ответ в беседу с цитированием исходного сообщения."""
        text = format_bot_message(text)
        base_params: dict[str, Any] = {
            "peer_id": peer_id,
            "message": text,
            "random_id": get_random_id(),
        }

        cmid = reply_to_cmid if reply_to_cmid is not None else self._conversation_message_id(event)
        params = dict(base_params)
        if cmid is not None:
            params["forward"] = json.dumps(
                {
                    "peer_id": peer_id,
                    "conversation_message_ids": [cmid],
                    "is_reply": 1,
                },
                ensure_ascii=False,
            )
        elif event.message_id:
            params["reply_to"] = event.message_id

        try:
            self._vk.messages.send(**params)
        except ApiError as error:
            if error.code != 100:
                raise
            logger.warning("Reply недоступен для chat peer_id=%s, отправляю обычным сообщением", peer_id)
            self._vk.messages.send(**base_params)

    def send_message(self, peer_id: int, text: str) -> None:
        """Обычное сообщение в беседу без reply."""
        self._vk.messages.send(
            peer_id=peer_id,
            message=format_bot_message(text),
            random_id=get_random_id(),
        )

    def _set_typing(self, peer_id: int) -> None:
        try:
            self._vk.messages.setActivity(peer_id=peer_id, type="typing")
        except ApiError:
            logger.debug("Не удалось отправить typing peer_id=%s", peer_id)

    def _delete_trigger_message(self, peer_id: int, event: Any) -> None:
        """Удаляет исходящее «артем …» владельца из беседы."""
        cmid = self._conversation_message_id(event)
        if cmid is None:
            logger.warning("Не удалось удалить триггер: нет conversation_message_id")
            return
        try:
            self._vk.messages.delete(
                peer_id=peer_id,
                cmids=cmid,
                delete_for_all=1,
            )
            logger.info("Удалено сообщение-триггер cmid=%s peer_id=%s", cmid, peer_id)
        except ApiError as error:
            logger.warning(
                "Не удалось удалить триггер cmid=%s peer_id=%s: %s",
                cmid,
                peer_id,
                error,
            )

    def _fusionbrain_configured(self) -> bool:
        return bool(
            self._settings.fusionbrain_api_key.strip()
            and self._settings.fusionbrain_secret_key.strip()
        )

    def _resolve_image_provider(self, requested: str) -> str:
        if requested in ("kandinsky", "horde"):
            return requested
        if self._fusionbrain_configured():
            return "kandinsky"
        return "horde"

    def _generate_narisuy_image(self, prompt: str, provider: str) -> tuple[bytes, str]:
        image_prompt = build_image_prompt(prompt)
        if provider == "kandinsky":
            if not self._fusionbrain_configured():
                raise RuntimeError(
                    "Kandinsky не настроен. Добавь FUSIONBRAIN_API_KEY и FUSIONBRAIN_SECRET_KEY "
                    "с https://fusionbrain.ai/keys/ или пиши «артем нарисуй horde …»"
                )
            image = generate_kandinsky_image(
                image_prompt,
                api_key=self._settings.fusionbrain_api_key,
                secret_key=self._settings.fusionbrain_secret_key,
            )
            return image, "Kandinsky"
        image = generate_horde_image(
            image_prompt,
            api_key=self._settings.stable_horde_api_key,
        )
        return image, "Stable Horde"

    def _send_narisuy_reply(self, peer_id: int, event: Any, command: Any) -> None:
        provider = self._resolve_image_provider(command.provider)
        image_bytes, provider_label = self._generate_narisuy_image(command.prompt, provider)
        caption = build_narisuy_caption(command.prompt, provider_label)
        send_image_bytes(
            self._vk_session,
            self._vk,
            peer_id,
            event,
            image_bytes,
            caption=caption,
            suffix=".jpg",
        )

    def _resolve_gif_query(self, command: Any, message_data: dict[str, Any] | None) -> str:
        if command.query:
            return command.query
        reply_msg = get_reply_message(message_data)
        if reply_msg is not None:
            source = reply_message_text(reply_msg).strip()
            if source and not source.startswith("["):
                return source[:120]
        return "facepalm reaction meme"

    def _send_gif_reply(
        self,
        peer_id: int,
        event: Any,
        command: Any,
        message_data: dict[str, Any] | None,
        *,
        reply_to_cmid: int | None = None,
    ) -> None:
        query = self._resolve_gif_query(command, message_data)
        gif_url, provider = search_gif(
            query,
            tenor_api_key=self._settings.tenor_api_key,
            giphy_api_key=self._settings.giphy_api_key,
            klipy_api_key=self._settings.klipy_api_key,
        )
        caption = f"Вот гифка, братан — {query[:50]}"
        send_gif_url(
            self._vk_session,
            self._vk,
            peer_id,
            event,
            gif_url,
            caption=caption,
            reply_to_cmid=reply_to_cmid,
        )
        logger.info("GIF через %s query=%r", provider, query[:80])

    def _send_meme_reply(
        self,
        peer_id: int,
        event: Any,
        command: Any,
        message_data: dict[str, Any] | None,
        *,
        reply_to_cmid: int | None = None,
    ) -> None:
        meme_bytes, top, bottom = build_meme(
            message_data=message_data,
            vk=self._vk,
            raw_text=command.text,
        )
        caption = build_meme_caption(top, bottom)
        send_image_bytes(
            self._vk_session,
            self._vk,
            peer_id,
            event,
            meme_bytes,
            caption=caption,
            suffix=".jpg",
            reply_to_cmid=reply_to_cmid,
        )

    def _send_quote_reply(
        self,
        peer_id: int,
        event: Any,
        message_data: dict[str, Any] | None,
        *,
        reply_to_cmid: int | None = None,
        style: str = "",
    ) -> None:
        quote_bytes, quote = build_quote_image(
            message_data=message_data,
            vk=self._vk,
            style=style,
        )
        send_image_bytes(
            self._vk_session,
            self._vk,
            peer_id,
            event,
            quote_bytes,
            caption="",
            suffix=".png",
            reply_to_cmid=reply_to_cmid,
            as_document=False,
        )
        logger.info("Цитата style=%r: %s — %r", style, quote.author_name[:40], quote.text[:60])

    async def _handle_narisuy(self, event: Any, command: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        logger.info(
            "Нарисуй chat_id=%s provider=%s prompt=%r",
            chat_id,
            command.provider,
            command.prompt[:80],
        )
        await asyncio.to_thread(self._set_typing, peer_id)
        await asyncio.to_thread(self._send_narisuy_reply, peer_id, event, command)

    async def _handle_gif(self, event: Any, command: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        message_data = _message_data(event)
        reply_to_cmid: int | None = None
        reply_msg = get_reply_message(message_data)
        if reply_msg is not None:
            reply_to_cmid = reply_message_conversation_id(reply_msg)
        logger.info("Гиф chat_id=%s query=%r", chat_id, command.query[:80])
        await asyncio.to_thread(self._set_typing, peer_id)
        await asyncio.to_thread(
            self._send_gif_reply,
            peer_id,
            event,
            command,
            message_data,
            reply_to_cmid=reply_to_cmid,
        )

    async def _handle_meme(self, event: Any, command: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        message_data = _message_data(event)
        reply_to_cmid: int | None = None
        reply_msg = get_reply_message(message_data)
        if reply_msg is not None:
            reply_to_cmid = reply_message_conversation_id(reply_msg)
        logger.info("Мем chat_id=%s text=%r", chat_id, command.text[:80])
        await asyncio.to_thread(self._set_typing, peer_id)
        await asyncio.to_thread(
            self._send_meme_reply,
            peer_id,
            event,
            command,
            message_data,
            reply_to_cmid=reply_to_cmid,
        )

    async def _handle_quote(self, event: Any, command: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        message_data = _message_data(event)
        reply_msg = get_reply_message(message_data)
        if reply_msg is None:
            logger.info("Цитата без reply chat_id=%s", chat_id)
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Ответь на сообщение человека с текстом — потом «артем цитата».",
                event,
            )
            return
        reply_to_cmid = reply_message_conversation_id(reply_msg)
        logger.info("Цитата chat_id=%s", chat_id)
        await asyncio.to_thread(self._set_typing, peer_id)
        await asyncio.to_thread(
            self._send_quote_reply,
            peer_id,
            event,
            message_data,
            reply_to_cmid=reply_to_cmid,
            style=command.style,
        )

    async def _handle_weather(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        text = (event.text or "").strip()
        city = parse_city_name(text)
        logger.info("Обработка /погода chat_id=%s city=%r", chat_id, city)

        if not city:
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Напиши город: /погода Москва",
                event,
            )
            return

        try:
            await asyncio.to_thread(self._set_typing, peer_id)
            report = await asyncio.to_thread(
                fetch_weather_report,
                city,
                self._settings.waqi_token,
            )
            await asyncio.to_thread(self.reply, peer_id, report, event)
            logger.info("Погода отправлена chat_id=%s city=%r", chat_id, city)
        except LookupError as exc:
            await asyncio.to_thread(self.reply, peer_id, str(exc), event)
        except RuntimeError as exc:
            await asyncio.to_thread(self.reply, peer_id, str(exc), event)
        except requests.RequestException:
            logger.exception("WAQI API /погода chat_id=%s", chat_id)
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Не достучался до aqicn.org — попробуй позже.",
                event,
            )
        except Exception:
            logger.exception("Ошибка /погода chat_id=%s", chat_id)
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Не смог получить погоду. Проверь название города.",
                event,
            )

    async def _handle_ava(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        text = (event.text or "").strip()
        logger.info("Обработка /ава chat_id=%s text=%r", chat_id, text[:120])

        try:
            reference = parse_profile_reference(text)
            user_id = await asyncio.to_thread(resolve_user_id, self._vk, reference, event)
            if user_id is None:
                await asyncio.to_thread(
                    self.reply,
                    peer_id,
                    "Не понял, чью аву кидать. Ответь на сообщение, скинь ссылку vk.com/id… или vk.com/ник.",
                    event,
                )
                return

            payload = await asyncio.to_thread(fetch_user_avatar, self._vk, user_id)
            await asyncio.to_thread(
                send_avatar_reply,
                self._vk_session,
                self._vk,
                peer_id,
                event,
                payload,
            )
            logger.info(
                "Ава отправлена chat_id=%s user_id=%s closed=%s attach=%s",
                chat_id,
                user_id,
                payload.is_closed,
                payload.attachment or "doc/url",
            )
        except LookupError as exc:
            await asyncio.to_thread(self.reply, peer_id, str(exc), event)
        except ApiError as exc:
            logger.exception("VK API /ава chat_id=%s", chat_id)
            await asyncio.to_thread(self.reply, peer_id, f"VK не дал аву: {exc}", event)
        except Exception:
            logger.exception("Ошибка /ава chat_id=%s", chat_id)
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Не смог достать аву. Попробуй ссылку или ответ на сообщение.",
                event,
            )

    def _resolve_access_target(self, event: Any, text: str, command: AccessCommand) -> int | None:
        target_id = reply_author_id(event)
        if target_id and target_id > 0:
            return target_id

        target_id = mention_user_id_from_text(text)
        if target_id:
            return target_id

        reference = (command.target_reference or "").strip()
        if not reference:
            return None

        parsed = parse_profile_reference(reference) or reference.lstrip("@")
        resolved = resolve_user_id(self._vk, parsed, event)
        if resolved and resolved > 0:
            return resolved
        return None

    async def _handle_info(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        report = format_ai_info(
            provider=self._settings.ai_provider,
            text_model=self._models.text_model,
            vision_model=self._models.vision_model,
            whisper_model=self._settings.ai_whisper_model,
        )
        logger.info("Обработка /инфо chat_id=%s", chat_id)
        await asyncio.to_thread(self.reply, peer_id, report, event)

    async def _handle_ii(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        text = (event.text or "").strip()
        command = parse_ii_command(text)
        if command is None:
            return

        logger.info("Обработка /ии chat_id=%s action=%s", chat_id, command.action)

        if command.action == "list":
            report = format_models_list(self._models.text_model, self._models.vision_model)
        elif command.action == "set_text" and command.model_id:
            self._models.set_text_model(command.model_id)
            report = (
                f"Текстовая модель: {command.model_id}\n"
                f"Фото/видео без изменений: {self._models.vision_model}"
            )
        elif command.action == "set_vision" and command.model_id:
            self._models.set_vision_model(command.model_id)
            report = (
                f"Модель фото/видео: {command.model_id}\n"
                f"Текст без изменений: {self._models.text_model}"
            )
        elif command.action == "invalid":
            report = format_invalid_model(command.model_id or "?")
        else:
            report = format_models_status(self._models.text_model, self._models.vision_model)

        await asyncio.to_thread(self.reply, peer_id, report, event)

    async def _handle_delete(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        logger.info("Фейк /удалить chat_id=%s", chat_id)

        try:
            for index, message in enumerate(FAKE_DELETE_MESSAGES):
                if index:
                    await asyncio.sleep(2)
                await asyncio.to_thread(self.send_message, peer_id, message)
            logger.info("Фейк /удалить завершён chat_id=%s", chat_id)
        except Exception:
            logger.exception("Ошибка фейк /удалить chat_id=%s", chat_id)

    async def _handle_dov_list(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        users = self._access.list_users(peer_id)
        logger.info("Обработка /дов chat_id=%s entries=%d", chat_id, len(users))

        if not users:
            report = "В дов никого нет. Выдай доступ: /+дов 20 @ник"
        else:
            lines = ["В дов в этой беседе:"]
            for user_id, remaining in sorted(users.items(), key=lambda item: item[1], reverse=True):
                name = self._resolve_roast_target_from_id(user_id)
                word = "запрос" if remaining % 10 == 1 and remaining % 100 != 11 else "запросов"
                if remaining % 10 in (2, 3, 4) and remaining % 100 not in (12, 13, 14):
                    word = "запроса"
                lines.append(f"• {name} — {remaining} {word}")
            report = "\n".join(lines)

        await asyncio.to_thread(self.reply, peer_id, report, event)

    async def _handle_access(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        text = (event.text or "").strip()
        command = parse_access_command(text)
        if command is None:
            return

        target_id = self._resolve_access_target(event, text, command)
        if not target_id or target_id <= 0:
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Ответь на сообщение, упомяни человека или укажи @ник: /+дов 20 @username",
                event,
            )
            return

        if target_id == self._my_user_id:
            await asyncio.to_thread(
                self.reply,
                peer_id,
                "Себе доступ не нужен — ты и так можешь писать «артем …».",
                event,
            )
            return

        name = self._resolve_roast_target_from_id(target_id)
        action = command.action
        if action == "grant":
            quota = command.quota
            if quota is None:
                reply = "Укажи число запросов, например: /+дов 20"
            else:
                previous = self._access.grant(peer_id, target_id, quota)
                if previous is None:
                    reply = f"{name} получил {quota} запросов к ИИ. Пусть пишет «артем …»."
                elif previous == quota:
                    reply = f"У {name} уже {quota} запросов к ИИ."
                else:
                    reply = f"{name}: было {previous}, теперь {quota} запросов к ИИ."
        elif self._access.revoke(peer_id, target_id):
            reply = f"У {name} забрал доступ к ИИ."
        else:
            reply = f"У {name} и так не было доступа к ИИ."

        logger.info("Доступ %s user_id=%s chat_id=%s", action, target_id, chat_id)
        await asyncio.to_thread(self.reply, peer_id, reply, event)

    async def _handle_ai(self, event: Any) -> None:
        peer_id = event.peer_id
        chat_id = event.chat_id
        user_id = event.user_id
        text = (event.text or "").strip()
        outgoing_trigger = event.from_me and not event.to_me
        has_media = event_has_media(event)
        has_videos = event_has_videos(event)

        if text and should_ignore_message(text):
            logger.info("Пропуск (предохранитель) chat_id=%s: %r", chat_id, text[:80])
            return

        message_data = _message_data(event)
        prompt = resolve_prompt(text, has_media, count_photos(message_data))
        if prompt is None:
            logger.info("Пропуск (нет префикса %r) chat_id=%s: %r", TRIGGER_PREFIX, chat_id, text[:80])
            return

        cleaned_prompt = strip_tts_keyword(prompt)

        narisuy_cmd = parse_narisuy_command(cleaned_prompt)
        gif_cmd = parse_gif_command(cleaned_prompt)
        meme_cmd = parse_meme_command(cleaned_prompt)
        quote_cmd = parse_quote_command(cleaned_prompt)
        if (
            narisuy_cmd is not None
            or gif_cmd is not None
            or meme_cmd is not None
            or quote_cmd is not None
        ):
            if not outgoing_trigger:
                if user_id == self._my_user_id:
                    return
                if not self._access.has_access(peer_id, user_id):
                    logger.info("Пропуск (нет доступа) media chat_id=%s user_id=%s", chat_id, user_id)
                    return
            try:
                if narisuy_cmd is not None:
                    await self._handle_narisuy(event, narisuy_cmd)
                elif gif_cmd is not None:
                    await self._handle_gif(event, gif_cmd)
                elif meme_cmd is not None:
                    await self._handle_meme(event, meme_cmd)
                elif quote_cmd is not None:
                    await self._handle_quote(event, quote_cmd)
                if not outgoing_trigger and user_id != self._my_user_id:
                    self._access.consume(peer_id, user_id)
            except RuntimeError as exc:
                logger.warning("Media-команда chat_id=%s: %s", chat_id, exc)
                await asyncio.to_thread(self.reply, peer_id, str(exc), event)
            except Exception:
                logger.exception("Ошибка media-команды chat_id=%s", chat_id)
                if quote_cmd is not None:
                    fail_text = "Не вышло отправить цитату в VK — подожди пару сек и попробуй снова."
                else:
                    fail_text = "Не вышло — нет фото, не тот формат или генератор лагает. Проверь /инфо."
                await asyncio.to_thread(self.reply, peer_id, fail_text, event)
            return

        kratko_cmd = parse_kratko_command(cleaned_prompt)
        spor_cmd = parse_spor_command(cleaned_prompt)
        reply_cmd = parse_reply_to_user_command(cleaned_prompt)
        reply_to_user: ReplyToUserContext | None = None
        reply_to_cmid: int | None = None
        delete_owner_trigger = False
        ai_prompt = cleaned_prompt

        if kratko_cmd is not None:
            reply_msg = get_reply_message(message_data)
            if reply_msg is None:
                logger.info("Кратко без reply_message chat_id=%s", chat_id)
                await asyncio.to_thread(
                    self.reply,
                    peer_id,
                    "Ответь на сообщение и напиши «артем кратко».",
                    event,
                )
                return
            ai_prompt = build_kratko_prompt(
                reply_message_text(reply_msg),
                kratko_cmd.extra,
            )
            logger.info("Режим кратко chat_id=%s source=%r", chat_id, reply_message_text(reply_msg)[:80])
        elif spor_cmd is not None:
            reply_to_user = self._build_reply_to_user_context(
                event,
                message_data,
                build_spor_extra_instruction(spor_cmd.extra),
            )
            if reply_to_user is None:
                logger.info("Спорь без reply_message chat_id=%s", chat_id)
                await asyncio.to_thread(
                    self.reply,
                    peer_id,
                    "Сначала ответь (reply) на его сообщение, потом «артем спорь».",
                    event,
                )
                return
            reply_to_cmid = reply_to_user.reply_cmid
            ai_prompt = spor_cmd.extra or "спорь с ним — жёстко, не соглашайся"
            logger.info(
                "Режим спорь chat_id=%s target=%s cmid=%s text=%r",
                chat_id,
                reply_to_user.target_name,
                reply_to_cmid,
                reply_to_user.replied_text[:80],
            )
        elif reply_cmd is not None:
            reply_to_user = self._build_reply_to_user_context(
                event,
                message_data,
                reply_cmd.extra,
            )
            if reply_to_user is None:
                logger.info("Reply-to-user без reply_message chat_id=%s", chat_id)
                await asyncio.to_thread(
                    self.reply,
                    peer_id,
                    "Сначала ответь (reply) на сообщение человека, потом напиши «артем ответь ему».",
                    event,
                )
                return
            reply_to_cmid = reply_to_user.reply_cmid
            ai_prompt = reply_cmd.extra or "ответь на его сообщение"
            logger.info(
                "Reply-to-user chat_id=%s target=%s cmid=%s text=%r",
                chat_id,
                reply_to_user.target_name,
                reply_to_cmid,
                reply_to_user.replied_text[:80],
            )
            delete_owner_trigger = outgoing_trigger

        if outgoing_trigger:
            logger.info("Исходящий триггер в беседе chat_id=%s: %r", chat_id, text[:120])
        else:
            if user_id == self._my_user_id:
                logger.debug("Пропуск: сообщение от собственного user_id")
                return
            if not self._access.has_access(peer_id, user_id):
                logger.info("Пропуск (нет доступа к ИИ) chat_id=%s user_id=%s", chat_id, user_id)
                return
            remaining = self._access.get_remaining(peer_id, user_id)
            logger.info(
                "Входящее в беседе chat_id=%s от user_id=%s (осталось %s): %r",
                chat_id,
                user_id,
                remaining,
                text[:120],
            )

        if delete_owner_trigger:
            await asyncio.to_thread(self._delete_trigger_message, peer_id, event)

        try:
            await asyncio.to_thread(self._set_typing, peer_id)

            owner_self_media = outgoing_trigger and (
                media_in_current_message(message_data) or media_is_self_upload(message_data)
            )
            need_roast = outgoing_trigger and not owner_self_media and reply_to_user is None
            need_transcript = has_videos and should_transcribe_video(
                ai_prompt,
                mode=self._settings.transcribe_video,
            )

            media_task = asyncio.to_thread(
                load_message_images,
                self._vk_session.http,
                message_data,
                self._vk,
            )
            transcript_task = (
                asyncio.to_thread(
                    transcribe_videos_in_message,
                    self._vk_session.http,
                    self._vk,
                    message_data,
                    api_key=self._settings.ai_api_key,
                    base_url=self._settings.ai_base_url,
                    model=self._settings.ai_whisper_model,
                )
                if need_transcript
                else asyncio.sleep(0, result=None)
            )
            roast_task = (
                asyncio.to_thread(self._resolve_roast_target, event, peer_id)
                if need_roast
                else asyncio.sleep(0, result=None)
            )

            (
                (photo_data_urls, video_data_urls, has_photos, has_video, media_context),
                video_transcript,
                roast_target,
            ) = await asyncio.gather(media_task, transcript_task, roast_task)
            if outgoing_trigger:
                media_context = sanitize_media_context_for_owner(media_context, self._owner_names)
            image_count = len(photo_data_urls) + len(video_data_urls)
            wants_compare = wants_photo_compare(prompt, count_photos(message_data))
            if has_media and not image_count:
                reply = (
                    "Не смог открыть фото или видео из сообщения."
                    if outgoing_trigger
                    else "Не смог открыть твоё фото или видео. Даже файл у тебя кривой."
                )
            elif wants_compare and len(photo_data_urls) < 2:
                reply = "Для сравнения нужны два фото — приложи второе или ответь на сообщение с фото."
            else:
                compare_photos = len(photo_data_urls) >= 2
                if image_count:
                    logger.info(
                        "Кадров для анализа: %d фото + %d видео (compare=%s)",
                        len(photo_data_urls),
                        len(video_data_urls),
                        compare_photos,
                    )
                if media_context:
                    logger.info("Метаданные медиа: %s", media_context[:120])
                if need_transcript and video_transcript:
                    logger.info(
                        "Расшифровка видео (%d симв.): %s",
                        len(video_transcript),
                        video_transcript[:120],
                    )
                if outgoing_trigger:
                    logger.info(
                        "Режим хозяина: self_media=%s%s",
                        owner_self_media,
                        "" if owner_self_media else f" roast_target={roast_target!r}",
                    )
                reply = await self._ai.generate_reply(
                    ai_prompt,
                    photo_data_urls=photo_data_urls or None,
                    video_data_urls=video_data_urls or None,
                    chat_context=None,
                    has_photos=has_photos,
                    has_video=has_video or has_videos,
                    media_context=media_context,
                    compare_photos=compare_photos,
                    owner_trigger=outgoing_trigger,
                    roast_target=roast_target if reply_to_user is None else reply_to_user.target_name,
                    owner_self_media=owner_self_media,
                    owner_labels=self._owner_labels if outgoing_trigger else None,
                    owner_names=self._owner_names if outgoing_trigger else (),
                    video_transcript=video_transcript,
                    reply_to_user=reply_to_user,
                )
        except Exception:
            logger.exception("Ошибка AI API для user_id=%s", user_id)
            reply = "Сейчас не отвечаю — не трать моё и своё время."

        try:
            await asyncio.to_thread(
                self.reply,
                peer_id,
                reply,
                event,
                reply_to_cmid=reply_to_cmid,
            )
            logger.info(
                "Reply отправлен в беседу chat_id=%s (%d символов)%s",
                chat_id,
                len(reply),
                f" -> cmid={reply_to_cmid}" if reply_to_cmid else "",
            )
            if not outgoing_trigger and user_id != self._my_user_id:
                left = self._access.consume(peer_id, user_id)
                if left is not None and left == 0:
                    logger.info("Квота ИИ исчерпана user_id=%s chat_id=%s", user_id, chat_id)
        except Exception:
            logger.exception("Ошибка отправки сообщения chat_id=%s", chat_id)

    async def run(self) -> None:
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def _log_asyncio_failure(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            exc = context.get("exception")
            if exc is not None:
                logger.exception(
                    "Необработанная ошибка asyncio [акк %s]: %s",
                    self._account_id,
                    context.get("message"),
                    exc_info=exc,
                )
            else:
                logger.error(
                    "Необработанная ошибка asyncio [акк %s]: %s",
                    self._account_id,
                    context,
                )

        loop.set_exception_handler(_log_asyncio_failure)

        listener = threading.Thread(
            target=vk_listener_thread,
            args=(
                self._vk_session,
                self._longpoll_preload,
                queue,
                loop,
                self._access,
                self._account_id,
            ),
            name=f"vk-longpoll-{self._account_id}",
            daemon=True,
        )
        listener.start()

        while True:
            kind, event = await queue.get()
            if kind == "weather":
                await self._handle_weather(event)
            elif kind == "ava":
                await self._handle_ava(event)
            elif kind == "info":
                await self._handle_info(event)
            elif kind == "ii":
                await self._handle_ii(event)
            elif kind == "delete":
                await self._handle_delete(event)
            elif kind == "dov":
                await self._handle_dov_list(event)
            elif kind == "access":
                await self._handle_access(event)
            else:
                asyncio.create_task(self._handle_ai(event))


async def amain() -> None:
    acquire_instance_lock()
    atexit.register(release_instance_lock)
    settings = load_settings()
    multi_account = len(settings.vk_user_tokens) > 1
    bots = [
        VkDmBot(
            settings,
            vk_token=token,
            account_id=str(index + 1),
            multi_account=multi_account,
        )
        for index, token in enumerate(settings.vk_user_tokens)
    ]
    logger.info("Запуск %d VK-аккаунт(ов)", len(bots))
    await asyncio.gather(*(bot.run() for bot in bots))


def main() -> None:
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C")
    except requests.exceptions.ConnectionError:
        logger.error(
            "Не удалось подключиться к api.vk.com — нет интернета или DNS не резолвит VK. "
            "Проверь сеть и попробуй снова; при блокировке VK включи VPN."
        )
        sys.exit(1)
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)
    except Exception:
        logger.exception("Бот остановлен из-за неожиданной ошибки")
        sys.exit(1)


if __name__ == "__main__":
    main()
