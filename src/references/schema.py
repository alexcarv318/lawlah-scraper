from dataclasses import dataclass, field

from src.knowledge.models.references import CitationKind


@dataclass
class DiscourseState:
    current_act: str | None = None
    current_case: str | None = None
    current_rules: str | None = None


@dataclass(frozen=True)
class ExtractedReference:
    source_paragraph_id: int
    quoted_text: str
    kind: CitationKind
    alias_short_name: str | None
    title: str | None
    neutral_citation: str | None
    slr_citation: str | None
    edition: str | None
    provision_citations: tuple[str, ...] = field(default_factory=tuple)
    paragraph_pins: tuple[int, ...] = field(default_factory=tuple)
