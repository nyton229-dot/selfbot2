"""Извлечение фото и кадров видео из сообщений VK."""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Any

import requests

logger = logging.getLogger(__name__)

MAX_PHOTOS = 3
MAX_VIDEO_FRAMES = 4
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_VIDEO_BYTES = 25 * 1024 * 1024
_AI_PHOTO_MAX_WIDTH = 807
_FRAME_RATIOS = (0.1, 0.35, 0.65, 0.9)


def _iter_media_attachment_lists(message_data: dict[str, Any] | None) -> list[list[dict[str, Any]]]:
    """Вложения: сначала из сообщения, затем из reply_message."""
    if not message_data:
        return []

    sources: list[list[dict[str, Any]]] = []
    current = message_data.get("attachments")
    if isinstance(current, list) and current:
        sources.append(current)

    reply = message_data.get("reply_message")
    if isinstance(reply, dict):
        reply_attachments = reply.get("attachments")
        if isinstance(reply_attachments, list) and reply_attachments:
            sources.append(reply_attachments)
    return sources


def _attachments_have_media(attachments: list[dict[str, Any]]) -> bool:
    for attachment in attachments:
        if attachment.get("type") in ("photo", "video", "video_message"):
            return True
    return False


def media_is_self_upload(message_data: dict[str, Any] | None) -> bool:
    """Медиа в текущем сообщении, а не только в reply."""
    if not message_data:
        return False
    current = message_data.get("attachments")
    if not isinstance(current, list) or not _attachments_have_media(current):
        return False
    reply = message_data.get("reply_message")
    if not isinstance(reply, dict):
        return True
    reply_attachments = reply.get("attachments")
    if not isinstance(reply_attachments, list):
        return True
    return not _attachments_have_media(reply_attachments)


def _largest_photo_url(photo: dict[str, Any]) -> str | None:
    sizes = photo.get("sizes") or []
    if sizes:
        compact = [
            item
            for item in sizes
            if int(item.get("width") or 0) <= _AI_PHOTO_MAX_WIDTH
        ]
        pool = compact or sizes
        best = max(pool, key=lambda item: item.get("width", 0) * item.get("height", 0))
        url = best.get("url")
        if isinstance(url, str) and url:
            return url

    orig = photo.get("orig_photo")
    if isinstance(orig, dict):
        url = orig.get("url")
        if isinstance(url, str) and url:
            return url

    for key in ("photo_807", "photo_604", "photo_1280", "photo_2560", "photo_130", "src", "url"):
        url = photo.get(key)
        if isinstance(url, str) and url:
            return url
    return None


def _photo_api_id(photo: dict[str, Any]) -> str | None:
    owner_id = photo.get("owner_id")
    photo_id = photo.get("id")
    if owner_id is None or photo_id is None:
        return None
    access_key = photo.get("access_key")
    if access_key:
        return f"photo{owner_id}_{photo_id}_{access_key}"
    return f"photo{owner_id}_{photo_id}"


def _fetch_photo_url_by_id(vk: Any, photo: dict[str, Any]) -> str | None:
    photo_id = _photo_api_id(photo)
    if not photo_id:
        return None
    try:
        items = vk.photos.getById(photos=photo_id, photo_sizes=True)
    except Exception:
        logger.exception("photos.getById не удался для %s", photo_id)
        return None
    if not items:
        return None
    return _largest_photo_url(items[0])


def _photo_url_from_attachment(attachment: dict[str, Any], vk: Any | None = None) -> str | None:
    if attachment.get("type") != "photo":
        return None
    photo = attachment.get("photo") or {}
    url = _largest_photo_url(photo)
    if url:
        return url
    if vk is not None:
        return _fetch_photo_url_by_id(vk, photo)
    return None


def get_photo_urls(message_data: dict[str, Any] | None, vk: Any | None = None) -> list[str]:
    return [url for url, _label in get_photo_entries(message_data, vk)]


def count_photos(message_data: dict[str, Any] | None) -> int:
    total = 0
    for attachments in _iter_media_attachment_lists(message_data):
        for attachment in attachments:
            if attachment.get("type") == "photo":
                total += 1
    return total


def get_photo_entries(
    message_data: dict[str, Any] | None,
    vk: Any | None = None,
) -> list[tuple[str, str]]:
    if not message_data:
        return []

    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for index, attachments in enumerate(_iter_media_attachment_lists(message_data)):
        source = "твоё сообщение" if index == 0 else "сообщение, на которое ответили"
        for attachment in attachments:
            url = _photo_url_from_attachment(attachment, vk)
            if not url or url in seen:
                continue
            seen.add(url)
            entries.append((url, source))
            if len(entries) >= MAX_PHOTOS:
                return entries
    return entries


def _best_preview_url(items: list[dict[str, Any]] | None) -> str | None:
    if not items:
        return None
    best = max(items, key=lambda s: s.get("width", 0) * s.get("height", 0))
    return best.get("url")


def _preview_urls_from_video_obj(video: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for key in ("first_frame", "image"):
        best = _best_preview_url(video.get(key))
        if best and best not in urls:
            urls.append(best)
    for key in ("photo_1280", "photo_800", "photo_640", "photo_320", "photo_130"):
        url = video.get(key)
        if isinstance(url, str) and url and url not in urls:
            urls.append(url)
    return urls


def _collect_videos_from_attachments(attachments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for attachment in attachments:
        att_type = attachment.get("type")
        if att_type == "video":
            video = attachment.get("video")
            if isinstance(video, dict):
                items.append(video)
        elif att_type == "video_message":
            video = attachment.get("video_message")
            if isinstance(video, dict):
                items.append(video)
    return items


def get_video_items(message_data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not message_data:
        return []
    items: list[dict[str, Any]] = []
    for attachments in _iter_media_attachment_lists(message_data):
        items.extend(_collect_videos_from_attachments(attachments))
    return items


def find_video_object(message_data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Видео из вложений или из reply_message."""
    items = get_video_items(message_data)
    return items[0] if items else None


def _collect_video_urls(video: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    files = video.get("files") or {}

    def add(url: Any) -> None:
        if isinstance(url, str) and url.startswith("http") and url not in seen:
            seen.add(url)
            urls.append(url)

    mp4_keys = sorted(
        (key for key in files if key.startswith("mp4_")),
        key=lambda key: int(key.split("_", 1)[1]) if key.split("_", 1)[1].isdigit() else 0,
        reverse=True,
    )
    for key in mp4_keys:
        add(files.get(key))
    for key in ("hls", "hls_live", "dash", "src", "external"):
        add(files.get(key))
    add(video.get("direct_url"))
    add(video.get("link_mp4"))
    return urls


def _is_valid_video_file(path: str) -> bool:
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    if size < 1024:
        return False
    with open(path, "rb") as file:
        head = file.read(16)
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return True
    if head[:4] == b"\x1aE\xdf\xa3":
        return True
    if head.startswith(b"RIFF") and b"AVI" in head:
        return True
    return False


def _download_video_url(
    session: requests.Session,
    url: str,
    video_path: str,
    max_bytes: int,
) -> bool:
    headers = {
        "Referer": "https://vk.com/",
        "Origin": "https://vk.com",
    }
    try:
        with session.get(url, timeout=120, stream=True, headers=headers) as response:
            response.raise_for_status()
            total = 0
            with open(video_path, "wb") as file:
                for chunk in response.iter_content(chunk_size=65536):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > max_bytes:
                        logger.warning("Видео слишком большое по URL: %s", url[:80])
                        return False
                    file.write(chunk)
    except Exception:
        logger.exception("Не удалось скачать видео: %s", url[:80])
        return False
    return _is_valid_video_file(video_path)


def download_video_file(
    session: requests.Session,
    vk: Any,
    video: dict[str, Any],
    tmp_dir: str,
    max_bytes: int = MAX_VIDEO_BYTES,
) -> str:
    detailed = _fetch_video_details(vk, video)
    urls = [url for url in _collect_video_urls(detailed) if ".m3u8" not in url]
    if not urls:
        raise LookupError("Не удалось получить ссылку на видео")

    video_path = os.path.join(tmp_dir, "source.mp4")
    for index, url in enumerate(urls):
        if index > 0 and os.path.isfile(video_path):
            try:
                os.unlink(video_path)
            except OSError:
                pass
        if _download_video_url(session, url, video_path, max_bytes):
            logger.info("Видео скачано (%d/%d): %s", index + 1, len(urls), url[:80])
            return video_path

    raise LookupError("Не удалось скачать видео — VK отдал битый файл")


def message_has_videos(message_data: dict[str, Any] | None) -> bool:
    return find_video_object(message_data) is not None


def message_has_photos(message_data: dict[str, Any] | None) -> bool:
    for attachments in _iter_media_attachment_lists(message_data):
        for attachment in attachments:
            if attachment.get("type") == "photo":
                return True
    return False


def message_has_media(message_data: dict[str, Any] | None) -> bool:
    return message_has_photos(message_data) or message_has_videos(message_data)


def _video_api_id(video: dict[str, Any]) -> str | None:
    owner_id = video.get("owner_id")
    video_id = video.get("id")
    if owner_id is None or video_id is None:
        return None
    access_key = video.get("access_key")
    if access_key:
        return f"{owner_id}_{video_id}_{access_key}"
    return f"{owner_id}_{video_id}"


def _fetch_video_details(vk: Any, video: dict[str, Any]) -> dict[str, Any]:
    video_id = _video_api_id(video)
    if not video_id:
        return video

    try:
        response = vk.video.get(videos=video_id, extended=1)
        items = response.get("items") if isinstance(response, dict) else response
        if items:
            return items[0]
    except Exception:
        logger.exception("video.get не удался для %s", video_id)
    return video


def build_photo_context(message_data: dict[str, Any] | None) -> str | None:
    """Подписи к фото из VK — часто содержат имена."""
    lines: list[str] = []
    photo_no = 0
    for index, attachments in enumerate(_iter_media_attachment_lists(message_data)):
        source = "твоё сообщение" if index == 0 else "ответ"
        for attachment in attachments:
            if attachment.get("type") != "photo":
                continue
            photo_no += 1
            photo = attachment.get("photo") or {}
            caption = (photo.get("text") or "").strip()
            prefix = f"Фото {photo_no} ({source})"
            if caption:
                lines.append(f"{prefix}: {caption}")
            else:
                lines.append(f"{prefix}: без подписи")
    if not lines:
        return None
    if photo_no >= 2:
        lines.insert(0, f"Прикреплено {photo_no} фото — сравни их между собой.")
    return "\n".join(lines)


def build_media_context(
    message_data: dict[str, Any] | None, vk: Any | None = None
) -> str | None:
    parts: list[str] = []
    photo_context = build_photo_context(message_data)
    video_context = build_video_context(message_data, vk)
    if photo_context:
        parts.append(photo_context)
    if video_context:
        parts.append(video_context)
    if not parts:
        return None
    return "\n\n".join(parts)


def build_video_context(
    message_data: dict[str, Any] | None, vk: Any | None = None
) -> str | None:
    """Название и описание видео из VK — часто содержат имена."""
    lines: list[str] = []
    for video in get_video_items(message_data):
        detailed = _fetch_video_details(vk, video) if vk is not None else video
        title = (detailed.get("title") or "").strip()
        description = (detailed.get("description") or "").strip()
        duration = detailed.get("duration")
        if title:
            lines.append(f"Название видео: {title}")
        if description:
            lines.append(f"Описание: {description[:500]}")
        if duration:
            lines.append(f"Длительность: {duration} сек")
    if not lines:
        return None
    return "\n".join(lines)


def _pick_mp4_url(video: dict[str, Any]) -> str | None:
    files = video.get("files") or {}
    for key in ("mp4_1080", "mp4_720", "mp4_480", "mp4_360", "mp4_240"):
        url = files.get(key)
        if url:
            return url
    return None


def _frame_seconds(duration: int) -> tuple[float, ...]:
    if duration <= 0:
        return (0.0, 2.0, 5.0, 10.0, 18.0, 30.0, 45.0, 60.0)[:MAX_VIDEO_FRAMES]
    return tuple(min(duration - 0.1, duration * ratio) for ratio in _FRAME_RATIOS[:MAX_VIDEO_FRAMES])


def _download_mp4(session: requests.Session, mp4_url: str, tmp_dir: str) -> str | None:
    video_path = os.path.join(tmp_dir, "video.mp4")
    content = session.get(mp4_url, timeout=120).content
    if len(content) > MAX_VIDEO_BYTES:
        logger.warning("Видео слишком большое: %d байт", len(content))
        return None
    with open(video_path, "wb") as file:
        file.write(content)
    return video_path


def _encode_frame_jpeg(frame: Any) -> str | None:
    try:
        import cv2
    except ImportError:
        return None

    height, width = frame.shape[:2]
    if max(height, width) > 1280:
        scale = 1280 / max(height, width)
        frame = cv2.resize(frame, (int(width * scale), int(height * scale)))

    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
    if not ok:
        return None
    encoded = base64.b64encode(buffer.tobytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _extract_frames_with_opencv(
    session: requests.Session, mp4_url: str, duration: int = 0
) -> list[str]:
    try:
        import cv2
    except ImportError:
        return []

    data_urls: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = _download_mp4(session, mp4_url, tmp_dir)
        if not video_path:
            return []

        capture = cv2.VideoCapture(video_path)
        if not capture.isOpened():
            return []

        total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0)
        if duration <= 0 and total_frames > 0 and fps > 0:
            duration = int(total_frames / fps)

        if total_frames > 1:
            positions = [
                max(0, min(total_frames - 1, int(total_frames * ratio)))
                for ratio in _FRAME_RATIOS[:MAX_VIDEO_FRAMES]
            ]
        else:
            positions = [0]

        for frame_index in positions:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            data_url = _encode_frame_jpeg(frame)
            if data_url:
                data_urls.append(data_url)
        capture.release()

    if data_urls:
        logger.info("Кадры видео через OpenCV: %d", len(data_urls))
    return data_urls[:MAX_VIDEO_FRAMES]


def _extract_frames_with_ffmpeg(
    session: requests.Session, mp4_url: str, duration: int = 0
) -> list[str]:
    if not shutil.which("ffmpeg"):
        return []

    data_urls: list[str] = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        video_path = _download_mp4(session, mp4_url, tmp_dir)
        if not video_path:
            return []

        for index, second in enumerate(_frame_seconds(duration)):
            if index >= MAX_VIDEO_FRAMES:
                break
            frame_path = os.path.join(tmp_dir, f"frame_{index:02d}.jpg")
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(second),
                    "-i",
                    video_path,
                    "-frames:v",
                    "1",
                    "-q:v",
                    "2",
                    frame_path,
                ],
                capture_output=True,
                check=False,
            )
            if result.returncode != 0 or not os.path.isfile(frame_path):
                continue
            with open(frame_path, "rb") as file:
                encoded = base64.b64encode(file.read()).decode("ascii")
            data_urls.append(f"data:image/jpeg;base64,{encoded}")

    if data_urls:
        logger.info("Кадры видео через ffmpeg: %d", len(data_urls))
    return data_urls[:MAX_VIDEO_FRAMES]


def download_as_data_url(session: requests.Session, url: str) -> str:
    response = session.get(url, timeout=20)
    response.raise_for_status()

    content = response.content
    if len(content) > MAX_IMAGE_BYTES:
        raise RuntimeError(f"Изображение слишком большое: {len(content)} байт")

    mime = (response.headers.get("Content-Type") or "image/jpeg").split(";")[0].strip()
    if not mime.startswith("image/"):
        mime = "image/jpeg"

    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _load_video_frames(
    session: requests.Session, vk: Any | None, message_data: dict[str, Any] | None
) -> list[str]:
    data_urls: list[str] = []
    for video in get_video_items(message_data):
        detailed = _fetch_video_details(vk, video) if vk is not None else video
        duration = int(detailed.get("duration") or 0)
        mp4 = _pick_mp4_url(detailed)

        frames: list[str] = []
        if mp4:
            frames = _extract_frames_with_opencv(session, mp4, duration)
            if not frames:
                frames = _extract_frames_with_ffmpeg(session, mp4, duration)

        if frames:
            data_urls.extend(frames)
            continue

        logger.info("mp4 недоступен, использую превью VK")
        for url in _preview_urls_from_video_obj(detailed):
            try:
                data_urls.append(download_as_data_url(session, url))
            except Exception:
                logger.exception("Не удалось загрузить кадр видео: %s", url[:80])
            if len(data_urls) >= MAX_VIDEO_FRAMES:
                break

    return data_urls[:MAX_VIDEO_FRAMES]


def load_message_images(
    session: requests.Session,
    message_data: dict[str, Any] | None,
    vk: Any | None = None,
) -> tuple[list[str], list[str], bool, bool, str | None]:
    """
    Возвращает data URL фото, data URL кадров видео, флаги медиа и метаданные.
    """
    photo_data_urls: list[str] = []
    has_video = message_has_videos(message_data)
    has_photos = message_has_photos(message_data)
    media_context = build_media_context(message_data, vk) if has_video or has_photos else None

    photo_entries = get_photo_entries(message_data, vk)
    for url, source in photo_entries:
        try:
            photo_data_urls.append(download_as_data_url(session, url))
        except Exception:
            logger.exception("Не удалось загрузить фото (%s): %s", source, url[:80])

    video_data_urls = _load_video_frames(session, vk, message_data)

    return (
        photo_data_urls[:MAX_PHOTOS],
        video_data_urls[:MAX_VIDEO_FRAMES],
        has_photos,
        has_video,
        media_context,
    )
