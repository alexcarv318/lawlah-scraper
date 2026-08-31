from dataclasses import dataclass


@dataclass(frozen=True)
class EmbeddingLimits:
    max_tokens: int = 8000
    batch_size: int = 100
    retry_limit: int = 3
    retry_backoff_seconds: float = 2.0


@dataclass(frozen=True)
class EmbeddingUnit:
    provision_id: int
    citation: str | None
    text: str
