"""Поиск ffmpeg и извлечение аудио из видео."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess

logger = logging.getLogger(__name__)


def find_ffmpeg() -> str | None:
    path = shutil.which("ffmpeg")
    if path:
        return path

    candidates: list[str] = []
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(
            os.path.join(local_app, "Microsoft", "WinGet", "Links", "ffmpeg.exe")
        )
        packages_dir = os.path.join(local_app, "Microsoft", "WinGet", "Packages")
        if os.path.isdir(packages_dir):
            for entry in os.scandir(packages_dir):
                if not entry.is_dir() or "ffmpeg" not in entry.name.casefold():
                    continue
                bin_path = os.path.join(entry.path, "bin", "ffmpeg.exe")
                if os.path.isfile(bin_path):
                    candidates.append(bin_path)
                for root, _, files in os.walk(entry.path):
                    if "ffmpeg.exe" not in files:
                        continue
                    candidates.append(os.path.join(root, "ffmpeg.exe"))
                    break

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return candidate

    for candidate in ("/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(candidate):
            return candidate

    bundled = _ffmpeg_from_imageio()
    if bundled:
        logger.info("ffmpeg из imageio-ffmpeg: %s", bundled)
        return bundled
    return None


def _ffmpeg_from_imageio() -> str | None:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.isfile(exe):
            return exe
    except Exception as exc:
        logger.debug("imageio-ffmpeg недоступен: %s", exc)
    return None


def extract_audio_wav(input_path: str, output_path: str, max_seconds: int = 90) -> bool:
    """Mono 16 kHz WAV для Whisper."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        logger.warning("ffmpeg не найден — аудио из видео не извлечь")
        return False

    command = [
        ffmpeg,
        "-y",
        "-i",
        input_path,
        "-t",
        str(max_seconds),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        output_path,
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        logger.debug("ffmpeg audio stderr: %s", (result.stderr or "")[-400:])
        return False
    return os.path.isfile(output_path) and os.path.getsize(output_path) > 44
