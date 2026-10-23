"""CLI-owned logging setup, shared by direct and batch entrypoints."""

import asyncio
import json
import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, date, datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, RLock, Thread
from typing import Literal
from uuid import uuid4

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from miniagent.diagnostics import heartbeat_interval, log_context, log_event


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MINIAGENT_", env_file=".env", extra="ignore"
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_file_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_dir: Path = Path.home() / ".miniagent" / "logs"
    log_file_enabled: bool = True
    log_retention_days: int = Field(default=14, ge=1)
    progress_interval_seconds: float = Field(default=15.0, ge=0, allow_inf_nan=False)


class _Formatter(logging.Formatter):
    def __init__(self, *, json_lines: bool) -> None:
        super().__init__()
        self.json_lines = json_lines

    def format(self, record: logging.LogRecord) -> str:
        # Legacy loggers may contain exception bodies or arbitrary external text.
        # Only explicitly structured progress records enter the diagnostic sinks.
        fields = getattr(record, "fields", {})
        event = record.msg if hasattr(record, "fields") else "diagnostic.unstructured"
        data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(
                timespec="milliseconds"
            ),
            "level": record.levelname,
            "logger": record.name,
            "event": event,
            **fields,
        }
        if self.json_lines:
            return json.dumps(data, ensure_ascii=False)
        details = " ".join(
            f"{key}={json.dumps(value, ensure_ascii=False)}"
            for key, value in fields.items()
        )
        return f"{data['timestamp']} {record.levelname} {event} {details}".rstrip()


class _SafeStream(logging.StreamHandler):
    def handleError(self, record: logging.LogRecord) -> None:
        pass


class _SafeFile(RotatingFileHandler):
    def __init__(self, path: Path, fallback: logging.Handler) -> None:
        super().__init__(
            path, maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        self.fallback = fallback
        self.failed = False

    def handleError(self, record: logging.LogRecord) -> None:
        if not self.failed:
            self.failed = True
            warning = logging.LogRecord(
                "miniagent",
                logging.WARNING,
                "",
                0,
                "logging.file_unavailable",
                (),
                None,
            )
            warning.fields = {}
            self.fallback.handle(warning)

    def emit(self, record: logging.LogRecord) -> None:
        if not self.failed:
            super().emit(record)


class _Delivery(logging.Handler):
    def __init__(self, sinks: list[logging.Handler]) -> None:
        super().__init__()
        self.queue: Queue[logging.LogRecord] = Queue(maxsize=4096)
        self.sinks = sinks
        self.stopping = Event()
        self.dropped = 0
        self.thread = Thread(
            target=self._deliver, name="miniagent-logging", daemon=True
        )
        self.thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        if record.msg == "operation.waiting" and self.queue.qsize() >= 3072:
            self.dropped += 1
            return
        try:
            self.queue.put_nowait(record)
        except Full:
            self.dropped += 1

    def _deliver(self) -> None:
        try:
            while not self.stopping.is_set() or not self.queue.empty():
                try:
                    record = self.queue.get(timeout=0.05)
                except Empty:
                    continue
                for sink in self.sinks:
                    if record.levelno >= sink.level:
                        sink.handle(record)
            if self.dropped:
                record = logging.LogRecord(
                    "miniagent",
                    logging.WARNING,
                    "",
                    0,
                    "logging.records_dropped",
                    (),
                    None,
                )
                record.fields = {"dropped_count": self.dropped}
                for sink in self.sinks:
                    sink.handle(record)
        finally:
            for sink in self.sinks:
                sink.close()

    def finish(self) -> None:
        self.stopping.set()
        # A blocked output device must not hold up interruption/cleanup indefinitely.
        self.thread.join(timeout=0.5)


_session: ContextVar[bool] = ContextVar("logging_session", default=False)
_configuration_lock = RLock()
_deliveries: dict[_Delivery, int] = {}
_original_level = logging.NOTSET


def _expire_logs(root: Path, days: int) -> None:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).date()
    for directory in root.iterdir():
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            directory_date = date.fromisoformat(directory.name)
        except ValueError:
            continue
        if directory_date >= cutoff:
            continue
        for path in directory.glob("*.jsonl*"):
            # Delete only files with our generated invocation names.
            stem = path.name.split(".jsonl")[0]
            if len(stem) == 32 and all(char in "0123456789abcdef" for char in stem):
                path.unlink(missing_ok=True)
        if not any(directory.iterdir()):
            directory.rmdir()


@contextmanager
def logging_session(settings: LogSettings | None = None) -> Iterator[None]:
    global _original_level
    if _session.get():
        yield
        return
    settings = settings or LogSettings()
    invocation_id = uuid4().hex
    console = _SafeStream(sys.stderr)
    console.setLevel(settings.log_level)
    console.setFormatter(_Formatter(json_lines=False))
    sinks: list[logging.Handler] = [console]
    path = None
    file_error = False
    if settings.log_file_enabled:
        try:
            root = settings.log_dir.expanduser()
            directory = root / datetime.now(UTC).strftime("%Y-%m-%d")
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"{invocation_id}.jsonl"
            file = _SafeFile(path, console)
            file.setLevel(settings.log_file_level)
            file.setFormatter(_Formatter(json_lines=True))
            sinks.append(file)
        except OSError:
            file_error = True
        else:
            try:
                _expire_logs(root, settings.log_retention_days)
            except OSError:
                file_error = True
    delivery = _Delivery(sinks)
    logger = logging.getLogger("miniagent")
    delivery.addFilter(
        lambda record: (
            getattr(record, "fields", {}).get("invocation_id") == invocation_id
        )
    )
    with _configuration_lock:
        if not _deliveries:
            _original_level = logger.level
        _deliveries[delivery] = min(sink.level for sink in sinks)
        logger.setLevel(min(_deliveries.values()))
        logger.addHandler(delivery)
    token = _session.set(True)
    interval_token = heartbeat_interval.set(settings.progress_interval_seconds)
    with log_context(invocation_id=invocation_id):
        try:
            log_event("invocation.started", log_path=path)
            if file_error:
                log_event("logging.file_unavailable", level=logging.WARNING)
            yield
        except BaseException as exc:
            code = getattr(exc, "exit_code", None)
            cancelled = isinstance(
                exc, (KeyboardInterrupt, asyncio.CancelledError)
            ) or code in (130, 143)
            log_event(
                "invocation.interrupted" if cancelled else "invocation.failed",
                level=logging.INFO if cancelled else logging.ERROR,
                error_type=type(exc).__name__,
                exit_code=code,
            )
            raise
        else:
            log_event("invocation.completed")
        finally:
            heartbeat_interval.reset(interval_token)
            _session.reset(token)
            with _configuration_lock:
                logger.removeHandler(delivery)
                del _deliveries[delivery]
                logger.setLevel(
                    min(_deliveries.values()) if _deliveries else _original_level
                )
            delivery.finish()
