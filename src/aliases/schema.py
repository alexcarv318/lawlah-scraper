from dataclasses import dataclass
from enum import StrEnum


class AliasKind(StrEnum):
    CASE = "case"
    ACT = "act"
    RULES = "rules"
    FACT = "fact"


@dataclass(frozen=True)
class ExtractedAlias:
    short_name: str
    expanded_text: str
    kind: AliasKind
    title: str | None
    neutral_citation: str | None
    slr_citation: str | None
    edition: str | None
