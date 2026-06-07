"""Доступ к ИИ: владелец без лимита, остальным — квота запросов в беседе."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

GRANT_PREFIXES = ("/+дов", "+дов", "/+доступ", "+доступ")
REVOKE_PREFIXES = ("/-дов", "-дов", "/-доступ", "-доступ")
DOV_LIST_COMMAND = "/дов"
DOV_LIST_BLOCKED_PREFIXES = (*GRANT_PREFIXES, *REVOKE_PREFIXES)
GRANT_RE = re.compile(
    r"^(?P<prefix>/\+дов|\+дов|/\+доступ|\+доступ)\s+(?P<count>\d+)(?:\s+(?P<tail>.+))?$",
    re.IGNORECASE,
)
MENTION_USER_ID_RE = re.compile(r"\[id(?P<id>\d+)\|[^\]]+\]", re.IGNORECASE)

_store: AiAccessStore | None = None


@dataclass(frozen=True)
class AccessCommand:
    action: str
    quota: int | None = None
    target_reference: str | None = None


def mention_user_id_from_text(text: str) -> int | None:
    match = MENTION_USER_ID_RE.search(text)
    if match:
        return int(match.group("id"))
    return None


def parse_access_command(text: str) -> AccessCommand | None:
    normalized = text.strip()
    lower = normalized.casefold()

    for prefix in REVOKE_PREFIXES:
        pl = prefix.casefold()
        if lower == pl:
            return AccessCommand("revoke")
        if lower.startswith(pl + " "):
            tail = normalized[len(prefix) :].strip()
            return AccessCommand("revoke", target_reference=tail or None)

    for prefix in GRANT_PREFIXES:
        if lower == prefix.casefold():
            return AccessCommand("grant")

    match = GRANT_RE.match(normalized)
    if match:
        quota = int(match.group("count"))
        tail = (match.group("tail") or "").strip() or None
        if quota > 0:
            return AccessCommand("grant", quota=quota, target_reference=tail)
        return AccessCommand("grant", target_reference=tail)

    return None


def is_dov_list_command(text: str) -> bool:
    normalized = text.strip().casefold()
    for prefix in DOV_LIST_BLOCKED_PREFIXES:
        blocked = prefix.casefold()
        if normalized == blocked or normalized.startswith(f"{blocked} "):
            return False
    return normalized == DOV_LIST_COMMAND.casefold() or normalized.startswith(
        f"{DOV_LIST_COMMAND.casefold()} "
    )


def init_access_store(path: Path, owner_id: int) -> AiAccessStore:
    global _store
    _store = AiAccessStore(path, owner_id)
    return _store


def get_access_store() -> AiAccessStore:
    if _store is None:
        raise RuntimeError("AiAccessStore не инициализирован")
    return _store


class AiAccessStore:
    def __init__(self, path: Path, owner_id: int) -> None:
        self._path = path
        self._owner_id = owner_id
        self._by_peer: dict[str, dict[str, int]] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.is_file():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Не удалось прочитать %s", self._path)
            return
        if not isinstance(raw, dict):
            return

        for peer_key, users in raw.items():
            if isinstance(users, list):
                migrated = {
                    str(uid): 1
                    for uid in users
                    if isinstance(uid, int) or str(uid).isdigit()
                }
                if migrated:
                    self._by_peer[str(peer_key)] = migrated
                continue
            if not isinstance(users, dict):
                continue
            cleaned: dict[str, int] = {}
            for user_key, remaining in users.items():
                try:
                    count = int(remaining)
                except (TypeError, ValueError):
                    continue
                if count > 0:
                    cleaned[str(user_key)] = count
            if cleaned:
                self._by_peer[str(peer_key)] = cleaned

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(self._by_peer, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_remaining(self, peer_id: int, user_id: int) -> int | None:
        if user_id == self._owner_id:
            return None
        return self._by_peer.get(str(peer_id), {}).get(str(user_id), 0)

    def has_access(self, peer_id: int, user_id: int) -> bool:
        if user_id == self._owner_id:
            return True
        return self.get_remaining(peer_id, user_id) > 0

    def list_users(self, peer_id: int) -> dict[int, int]:
        users = self._by_peer.get(str(peer_id), {})
        result: dict[int, int] = {}
        for user_key, remaining in users.items():
            try:
                count = int(remaining)
            except (TypeError, ValueError):
                continue
            if count > 0:
                result[int(user_key)] = count
        return result

    def grant(self, peer_id: int, user_id: int, quota: int) -> int | None:
        if user_id == self._owner_id or quota <= 0:
            return None
        key = str(peer_id)
        users = self._by_peer.setdefault(key, {})
        user_key = str(user_id)
        previous = users.get(user_key)
        users[user_key] = quota
        self._save()
        logger.info(
            "Квота ИИ user_id=%s peer_id=%s: %s -> %d",
            user_id,
            peer_id,
            previous if previous is not None else "новый",
            quota,
        )
        return previous

    def revoke(self, peer_id: int, user_id: int) -> bool:
        key = str(peer_id)
        users = self._by_peer.get(key)
        if not users or str(user_id) not in users:
            return False
        del users[str(user_id)]
        if not users:
            del self._by_peer[key]
        self._save()
        logger.info("Доступ к ИИ отозван user_id=%s peer_id=%s", user_id, peer_id)
        return True

    def consume(self, peer_id: int, user_id: int) -> int | None:
        if user_id == self._owner_id:
            return None
        key = str(peer_id)
        users = self._by_peer.get(key)
        if not users:
            return 0
        user_key = str(user_id)
        remaining = users.get(user_key, 0)
        if remaining <= 0:
            return 0
        remaining -= 1
        if remaining <= 0:
            del users[user_key]
            if not users:
                del self._by_peer[key]
        else:
            users[user_key] = remaining
        self._save()
        logger.info("Списан запрос ИИ user_id=%s peer_id=%s, осталось %d", user_id, peer_id, remaining)
        return remaining
