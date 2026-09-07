from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificationLimits:
    batch_size: int = 16
    progress_every: int = 64
