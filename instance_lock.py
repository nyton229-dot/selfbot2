"""Один экземпляр бота — параллельные процессы = лишние сессии VK."""

from __future__ import annotations

import os
import sys
from pathlib import Path

LOCK_PATH = Path(__file__).resolve().parent / ".lpbot.pid"
_MUTEX_NAME = "Local\\lpbot_vk_singleton"

_mutex_handle: int | None = None


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        exit_code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
        kernel32.CloseHandle(handle)
        return bool(ok) and exit_code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _acquire_windows_mutex() -> None:
    global _mutex_handle
    import ctypes

    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    if not handle:
        raise RuntimeError("Не удалось создать mutex для одиночного экземпляра бота.")
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        kernel32.CloseHandle(handle)
        raise RuntimeError(
            "Бот уже запущен (mutex). "
            "Второй экземпляр создаёт лишнюю сессию VK и дублирует ответы. "
            "Останови старый процесс python main.py."
        )
    _mutex_handle = handle


def acquire_instance_lock() -> None:
    if sys.platform == "win32":
        _acquire_windows_mutex()

    if LOCK_PATH.exists():
        try:
            pid = int(LOCK_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            pid = 0
        if _process_alive(pid):
            raise RuntimeError(
                f"Бот уже запущен (PID {pid}). "
                "Второй экземпляр создаёт лишнюю сессию VK и повышает риск бана. "
                "Останови старый процесс. Если он завис — удали файл .lpbot.pid."
            )
        LOCK_PATH.unlink(missing_ok=True)

    LOCK_PATH.write_text(str(os.getpid()), encoding="utf-8")


def release_instance_lock() -> None:
    global _mutex_handle
    try:
        if LOCK_PATH.exists() and int(LOCK_PATH.read_text(encoding="utf-8").strip()) == os.getpid():
            LOCK_PATH.unlink()
    except (ValueError, OSError):
        pass
    if sys.platform == "win32" and _mutex_handle:
        import ctypes

        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        _mutex_handle = None
