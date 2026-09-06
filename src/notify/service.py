from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from html import escape
from threading import Event, Thread

import httpx

from src.logger import get_logger
from src.notify.repository import (
    ChatRegistry,
    TelegramClient,
    start_command_chat_id,
    update_id,
)
from src.notify.schema import FailureItem, StageReport, StageReporter
from src.settings import get_settings

MAX_FAILURES_IN_MESSAGE = 12
MESSAGE_LIMIT = 3900
WELCOME_NEW = "Registered. You will get Lawlah pipeline updates."
WELCOME_AGAIN = "Already registered. You will keep getting Lawlah pipeline updates."
TELEGRAM_ERRORS = (httpx.HTTPError, RuntimeError, OSError)

logger = get_logger(__name__)


class TelegramListener:
    def __init__(self, client: TelegramClient, registry: ChatRegistry) -> None:
        self.client = client
        self.registry = registry
        self.stop_event = Event()
        self.thread = Thread(target=self.listen, name="telegram-listen", daemon=True)

    def start(self) -> None:
        self.collect_updates(timeout_seconds=0)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=8)

    def listen(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.collect_updates()
            except TELEGRAM_ERRORS as error:
                logger.warning("Telegram listen failed: %s", error)
                self.stop_event.wait(5)

    def collect_updates(self, timeout_seconds: int = 25) -> None:
        updates = self.client.get_updates(self.registry.offset, timeout_seconds)
        for update in updates:
            current_id = update_id(update)
            if current_id is not None:
                self.registry.advance_offset(current_id + 1)

            chat_id = start_command_chat_id(update)
            if chat_id is None:
                continue

            added = self.registry.register(chat_id)
            welcome = WELCOME_NEW if added else WELCOME_AGAIN
            self.client.send_message(chat_id, welcome)
            logger.info("Telegram /start from %s (%s)", chat_id, "new" if added else "existing")


class TelegramNotifier:
    def __init__(
        self,
        client: TelegramClient,
        registry: ChatRegistry,
        listener: TelegramListener,
    ) -> None:
        self.client = client
        self.registry = registry
        self.listener = listener
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="telegram")

    def send(self, text: str) -> None:
        self.pool.submit(self.send_safe, text)

    def send_safe(self, text: str) -> None:
        subscribers = self.registry.subscribers()
        if not subscribers:
            logger.warning("No Telegram subscribers. Send /start to the bot")
            return

        for chat_id in subscribers:
            try:
                self.client.send_message(chat_id, text)
            except TELEGRAM_ERRORS as error:
                logger.warning("Telegram notify failed for %s: %s", chat_id, error)

    def close(self) -> None:
        self.listener.stop()
        self.listener.client.close()
        self.pool.shutdown(wait=True)
        self.client.close()


class PipelineReporter:
    def __init__(self, job: str, notifier: TelegramNotifier) -> None:
        self.job = job
        self.notifier = notifier

    def started(self) -> None:
        self.notifier.send(f"<b>{escape(self.job)}</b>\nstarted")

    def progress(self, report: StageReport) -> None:
        self.notifier.send(self.format_report(report))

    def stage(self, report: StageReport) -> None:
        self.notifier.send(self.format_report(report))

    def finished(self) -> None:
        self.notifier.send(f"<b>{escape(self.job)}</b>\nfinished")

    def failed(self, error: BaseException) -> None:
        detail = f"{type(error).__name__}: {error}"
        self.notifier.send(f"<b>{escape(self.job)}</b>\nfailed\n<code>{escape(detail)}</code>")

    def close(self) -> None:
        self.notifier.close()

    def format_report(self, report: StageReport) -> str:
        lines = [f"<b>{escape(self.job)} · {escape(report.stage)}</b>"]
        progress = report.progress_label()
        if progress is not None:
            lines.append(progress)

        for name, value in report.counts.items():
            lines.append(f"{value} {name}")

        if report.note:
            lines.append(escape(report.note))

        lines.extend(format_failures(report.failures))
        text = "\n".join(lines)
        if len(text) <= MESSAGE_LIMIT:
            return text
        return text[: MESSAGE_LIMIT - 1] + "…"


def format_failures(failures: tuple[FailureItem, ...]) -> list[str]:
    if not failures:
        return []

    lines = [""]
    shown = failures[:MAX_FAILURES_IN_MESSAGE]
    for item in shown:
        lines.append(f"<code>{escape(item.identifier)}</code> — {escape(item.reason)}")
    hidden = len(failures) - len(shown)
    if hidden > 0:
        lines.append(f"+{hidden} more")
    return lines


def create_reporter(job: str) -> PipelineReporter | None:
    settings = get_settings()
    if not settings.bot_token:
        logger.warning("BOT_TOKEN is not set; Telegram notifications are off")
        return None

    send_client = TelegramClient(settings.bot_token)
    listen_client = TelegramClient(settings.bot_token)
    registry = ChatRegistry()
    listener = TelegramListener(listen_client, registry)
    listener.start()

    if not registry.subscribers():
        logger.warning("No Telegram subscribers yet. Send /start to the bot")

    return PipelineReporter(job, TelegramNotifier(send_client, registry, listener))


def run_job(job: str, action: Callable[[StageReporter | None], None]) -> None:
    reporter = create_reporter(job)

    try:
        if reporter is not None:
            reporter.started()

        action(reporter)

        if reporter is not None:
            reporter.finished()
    except Exception as error:
        if reporter is not None:
            reporter.failed(error)
        raise
    finally:
        if reporter is not None:
            reporter.close()


def listen_for_subscribers() -> None:
    settings = get_settings()
    if not settings.bot_token:
        raise ValueError("BOT_TOKEN is not set")

    client = TelegramClient(settings.bot_token)
    registry = ChatRegistry()
    listener = TelegramListener(client, registry)
    
    listener.start()
    logger.info(
        "Listening for /start (%s subscriber(s)). Ctrl+C to stop.",
        len(registry.subscribers()),
    )
    
    try:
        listener.stop_event.wait()
    except KeyboardInterrupt:
        logger.info("Stopped listening")
    finally:
        listener.stop()
        client.close()
