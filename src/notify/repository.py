import json
from pathlib import Path
from threading import Lock

import httpx

from src.logger import get_logger
from src.settings import PROJECT_ROOT

TELEGRAM_API = "https://api.telegram.org"
REQUEST_TIMEOUT_SECONDS = 30.0
LONG_POLL_SECONDS = 25
SUBSCRIBERS_PATH = PROJECT_ROOT / "data" / "telegram_chats.json"

logger = get_logger(__name__)


class TelegramClient:
    def __init__(self, token: str) -> None:
        self.token = token
        self.http = httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    def send_message(self, chat_id: str, text: str) -> None:
        response = self.http.post(
            self.method_url("sendMessage"),
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram sendMessage failed with status {response.status_code}")

    def get_updates(self, offset: int, timeout_seconds: int = LONG_POLL_SECONDS) -> list[object]:
        response = self.http.get(
            self.method_url("getUpdates"),
            params={"offset": offset, "timeout": timeout_seconds, "limit": 100},
            timeout=timeout_seconds + 5,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Telegram getUpdates failed with status {response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("Telegram getUpdates returned an unsuccessful payload")

        updates = payload.get("result")
        if not isinstance(updates, list):
            return []
        return list(updates)

    def close(self) -> None:
        self.http.close()

    def method_url(self, method: str) -> str:
        return f"{TELEGRAM_API}/bot{self.token}/{method}"


class ChatRegistry:
    def __init__(self, path: Path = SUBSCRIBERS_PATH) -> None:
        self.path = path
        self.lock = Lock()
        self.offset, self.chat_ids = self.load()

    def load(self) -> tuple[int, set[str]]:
        if not self.path.exists():
            return 0, set()

        raw = json.loads(self.path.read_text())
        if not isinstance(raw, dict):
            return 0, set()

        offset = raw.get("offset", 0)
        stored_offset = offset if isinstance(offset, int) and not isinstance(offset, bool) else 0

        stored_chats: set[str] = set()
        chat_ids = raw.get("chat_ids", [])
        if isinstance(chat_ids, list):
            for item in chat_ids:
                if isinstance(item, int | str):
                    stored_chats.add(str(item))

        return stored_offset, stored_chats

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "offset": self.offset,
            "chat_ids": sorted(self.chat_ids),
        }
        self.path.write_text(json.dumps(payload, indent=2) + "\n")

    def subscribers(self) -> set[str]:
        with self.lock:
            return set(self.chat_ids)

    def register(self, chat_id: str) -> bool:
        with self.lock:
            added = chat_id not in self.chat_ids
            self.chat_ids.add(chat_id)
            self.save()
            return added

    def advance_offset(self, next_offset: int) -> None:
        with self.lock:
            if next_offset > self.offset:
                self.offset = next_offset
                self.save()


def update_id(update: object) -> int | None:
    if not isinstance(update, dict):
        return None
    value = update.get("update_id")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def start_command_chat_id(update: object) -> str | None:
    if not isinstance(update, dict):
        return None

    for key in ("message", "channel_post"):
        message = update.get(key)
        if not isinstance(message, dict):
            continue
        text = message.get("text")
        if not isinstance(text, str) or not is_start_command(text):
            continue

        chat = message.get("chat")
        if not isinstance(chat, dict):
            continue

        chat_id = chat.get("id")
        if isinstance(chat_id, int | str):
            return str(chat_id)
    return None


def is_start_command(text: str) -> bool:
    command = text.split(maxsplit=1)[0] if text.strip() else ""
    return command == "/start" or command.startswith("/start@")
