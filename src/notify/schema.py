from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class FailureItem:
    identifier: str
    reason: str


@dataclass(frozen=True)
class StageReport:
    stage: str
    counts: dict[str, int]
    failures: tuple[FailureItem, ...] = ()
    note: str | None = None
    in_progress: bool = False
    done: int | None = None
    total: int | None = None

    def progress_label(self) -> str | None:
        if not self.in_progress:
            return None
        if self.done is not None and self.total is not None:
            return f"in progress — {self.done} of {self.total}"
        return "in progress"


class StageReporter(Protocol):
    def started(self) -> None: ...

    def progress(self, report: StageReport) -> None: ...

    def stage(self, report: StageReport) -> None: ...

    def finished(self) -> None: ...

    def failed(self, error: BaseException) -> None: ...

    def close(self) -> None: ...


def stored_failures(items: list[FailureItem], limit: int = 40) -> tuple[FailureItem, ...]:
    return tuple(items[:limit])


def emit_report(reporter: StageReporter | None, report: StageReport) -> None:
    if reporter is None:
        return
    if report.in_progress:
        reporter.progress(report)
        return
    reporter.stage(report)
