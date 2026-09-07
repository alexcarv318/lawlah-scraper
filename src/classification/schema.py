from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, Field


class ClassificationMode(StrEnum):
    LOCAL = "local"
    REMOTE = "remote"


@dataclass(frozen=True)
class Classification:
    role: str
    topics: tuple[str, ...]
    concepts: tuple[str, ...]


@dataclass(frozen=True)
class ClassificationLimits:
    batch_size: int = 16
    progress_every: int = 64


class ClassifyRequest(BaseModel):
    source: Literal["judgments", "legislation"]
    texts: list[str]
    previous_texts: list[str] = Field(default_factory=list)


class ClassifyItem(BaseModel):
    role: str
    topics: list[str]
    concepts: list[str]


class ClassifyResponse(BaseModel):
    items: list[ClassifyItem]


class HealthResponse(BaseModel):
    ok: bool
    ready: bool
    device: str


class ParagraphClassifier(Protocol):
    def classify(self, text: str, previous_text: str = "") -> Classification: ...

    def classify_many(
        self,
        texts: list[str],
        previous_texts: list[str] | None = None,
    ) -> list[Classification]: ...
