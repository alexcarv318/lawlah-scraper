import re
from dataclasses import dataclass
from re import Pattern

from src.aliases.extract import CaseAliasExtractor
from src.aliases.schema import AliasKind, ExtractedAlias
from src.database import get_knowledge_base_session_maker
from src.knowledge.models.aliases import Alias
from src.knowledge.models.paragraphs import Paragraph
from src.knowledge.models.references import CitationKind
from src.knowledge.repository import KnowledgeCaseRepository
from src.logger import get_logger
from src.references.resolve import ReferenceResolver
from src.references.schema import DiscourseState, ExtractedReference

logger = get_logger(__name__)


@dataclass(frozen=True)
class CandidateSpan:
    start: int
    end: int
    kind: CitationKind
    quoted_text: str
    alias_short_name: str | None
    title: str | None
    neutral_citation: str | None
    slr_citation: str | None
    edition: str | None
    provision_citations: tuple[str, ...]
    paragraph_pins: tuple[int, ...]


class CaseReferenceExtractor:
    def __init__(self) -> None:
        self.aliases = CaseAliasExtractor()
        pin_cluster = (
            r"\[[0-9]+\](?:\s*[–\-—]\s*\[[0-9]+\])?"
            r"(?:\s+and\s+\[[0-9]+\](?:\s*[–\-—]\s*\[[0-9]+\])?)*"
        )
        self.pin_cluster = pin_cluster
        self.after_case: Pattern[str] = re.compile(
            r"^(?P<alias>\s*\((?:the\s+)?[\"“]\s*[^\"”]+?\s*[\"”]"
            r"(?:\s+or\s+(?:the\s+)?[\"“]\s*[^\"”]+?\s*[\"”])?\))?"
            rf"(?P<pins>\s+at\s+{pin_cluster})?",
            re.IGNORECASE,
        )
        self.provision: Pattern[str] = re.compile(
            r"\b(?P<kind>ss?|Sections?|rules?|rr?)\s+"
            r"(?P<refs>"
            r"[0-9]+[A-Z]?(?:\(\s*[a-z0-9]+\s*\))*"
            r"(?:\s*(?:,|and)\s*[0-9]+[A-Z]?(?:\(\s*[a-z0-9]+\s*\))*)*"
            r")",
            re.IGNORECASE,
        )
        self.of_instrument: Pattern[str] = re.compile(
            r"^\s+of\s+(?:the\s+)?"
            r"(?P<name>"
            r"[A-Z][\w'’\-,]*(?:\s+[A-Z][\w'’\-,]+|\s+and|\s+of|\s+the|\s+for|\s+\([^)]+\))*"
            r"\s+(?:Act|Rules|Regulations|Code|Order)"
            r"(?:\s+\d{4})?"
            r")"
            r"(?:\s+\((?P<edition>Cap\.?\s+[^)]+|[^)]*Rev Ed)\))?"
        )
        self.leading_space: Pattern[str] = re.compile(r"^\s+")
        self.of_the: Pattern[str] = re.compile(r"^\s+of\s+(?:the\s+)?")
        self.above_pin: Pattern[str] = re.compile(
            rf"\babove\s+(?:at\s+|\(at\s+)(?P<pins>{pin_cluster})\)?",
            re.IGNORECASE,
        )
        self.described_at: Pattern[str] = re.compile(
            rf"\b(?:described|set out|outlined|mentioned)\s+at\s+(?P<pins>{pin_cluster})",
            re.IGNORECASE,
        )
        self.bare_pin: Pattern[str] = re.compile(
            rf"(?:\(at\s+|\bat\s+)(?P<pins>{pin_cluster})\)?",
            re.IGNORECASE,
        )
        self.re_case: Pattern[str] = re.compile(
            r"\bRe\s+[A-Z][A-Za-z0-9'’\-]+(?:\s+[A-Z][A-Za-z0-9'’\-]+){0,6}"
        )
        self.after_re: Pattern[str] = re.compile(
            rf"^(?P<pins>\s+at\s+{pin_cluster})?",
            re.IGNORECASE,
        )
        self.book: Pattern[str] = re.compile(
            r"(?P<title>[A-Z][^()]{8,120}?)\s+"
            r"\((?P<pub>[^)]*\d+(?:st|nd|rd|th)\s+Ed\.?[^)]*\d{4})\)"
            r"(?:\s+at\s+pp?\s+\d+(?:\s*[–\-—]\s*\d+)?)?"
        )
        self.word_boundary_end: Pattern[str] = re.compile(r"\w")

    def run(self, max_cases: int | None = None) -> None:
        session_maker = get_knowledge_base_session_maker()
        with session_maker() as session:
            repository = KnowledgeCaseRepository(session)
            cases = repository.get_cases_with_paragraphs(max_cases)
            total = 0

            for case, paragraphs in cases:
                stored = repository.get_aliases_for_case(case.id)
                extracted_aliases = CaseReferenceExtractor.aliases_from_rows(stored)
                extracted = self.extract_case(paragraphs, extracted_aliases)
                alias_ids = {row.short_name: row.id for row in stored}
                rows = repository.replace_references(case.id, extracted, alias_ids)
                resolved = ReferenceResolver.resolve_rows(repository, rows)
                session.commit()
                total += len(extracted)
                logger.info(
                    "References for %s: %s extracted, %s resolved",
                    case.neutral_citation,
                    len(extracted),
                    resolved,
                )

        logger.info(
            "Reference extraction finished: %s references across %s cases",
            total,
            len(cases),
        )

    def extract_case(
        self,
        paragraphs: list[Paragraph],
        aliases: list[ExtractedAlias],
    ) -> list[ExtractedReference]:
        state = DiscourseState()
        extracted: list[ExtractedReference] = []
        for paragraph in paragraphs:
            extracted.extend(self.extract_from_text(paragraph.id, paragraph.content, aliases, state))
        return extracted

    def extract_from_text(
        self,
        paragraph_id: int,
        text: str,
        aliases: list[ExtractedAlias],
        state: DiscourseState,
    ) -> list[ExtractedReference]:
        candidates = self.structured_candidates(text, aliases)
        occupied: list[tuple[int, int]] = []
        accepted: list[CandidateSpan] = []

        for candidate in sorted(candidates, key=lambda item: (item.start, -(item.end - item.start))):
            if CaseReferenceExtractor.is_occupied(candidate.start, candidate.end, occupied):
                continue
            finalized = CaseReferenceExtractor.with_current_instrument(candidate, state)
            accepted.append(finalized)
            occupied.append((finalized.start, finalized.end))
            CaseReferenceExtractor.update_state(state, finalized)

        for candidate in self.anaphoric_pins(text, state):
            if CaseReferenceExtractor.is_occupied(candidate.start, candidate.end, occupied):
                continue
            accepted.append(candidate)
            occupied.append((candidate.start, candidate.end))
            CaseReferenceExtractor.update_state(state, candidate)

        accepted.sort(key=lambda item: item.start)
        return [
            ExtractedReference(
                source_paragraph_id=paragraph_id,
                quoted_text=item.quoted_text,
                kind=item.kind,
                alias_short_name=item.alias_short_name,
                title=item.title,
                neutral_citation=item.neutral_citation,
                slr_citation=item.slr_citation,
                edition=item.edition,
                provision_citations=item.provision_citations,
                paragraph_pins=item.paragraph_pins,
            )
            for item in accepted
        ]

    def structured_candidates(self, text: str, aliases: list[ExtractedAlias]) -> list[CandidateSpan]:
        candidates: list[CandidateSpan] = []
        candidates.extend(self.case_citation_spans(text))
        candidates.extend(self.provision_spans(text, aliases))
        candidates.extend(self.self_spans(text))
        candidates.extend(self.book_spans(text))
        candidates.extend(self.re_spans(text))
        candidates.extend(self.alias_mention_spans(text, aliases))
        return candidates

    def case_citation_spans(self, text: str) -> list[CandidateSpan]:
        spans: list[CandidateSpan] = []
        for match in self.reporter_matches(text):
            title_start = self.title_start(text, match.start())
            trailing = self.after_case.match(text[match.end() :])
            end = match.end() + trailing.end() if trailing is not None else match.end()
            alias_name: str | None = None
            pins: tuple[int, ...] = ()
            if trailing is not None:
                if trailing.group("alias"):
                    alias_match = self.aliases.alias_definition.search(trailing.group("alias"))
                    if alias_match is not None:
                        names = CaseAliasExtractor.short_names_from_match(alias_match)
                        alias_name = names[0] if names else None
                if trailing.group("pins"):
                    pins = CaseReferenceExtractor.parse_pins(trailing.group("pins"))
            citation = match.group(0)
            title = text[title_start : match.start()].strip(" ,;(") or None
            spans.append(
                CandidateSpan(
                    start=title_start,
                    end=end,
                    kind=CitationKind.CASE,
                    quoted_text=text[title_start:end].strip(),
                    alias_short_name=alias_name,
                    title=title,
                    neutral_citation=citation if self.aliases.neutral_citation.fullmatch(citation) else None,
                    slr_citation=citation if self.aliases.slr_citation.fullmatch(citation) else None,
                    edition=None,
                    provision_citations=(),
                    paragraph_pins=pins,
                )
            )
        return spans

    def provision_spans(self, text: str, aliases: list[ExtractedAlias]) -> list[CandidateSpan]:
        spans: list[CandidateSpan] = []
        for match in self.provision.finditer(text):
            end = match.end()
            title: str | None = None
            edition: str | None = None
            alias_name: str | None = None
            tail = text[end:]
            instrument = self.of_instrument.match(tail)
            if instrument is not None:
                title = instrument.group("name").strip()
                edition = instrument.group("edition")
                end += instrument.end()
                spacing = self.leading_space.match(text[end:])
                if spacing is not None:
                    end += spacing.end()
                alias_def = self.aliases.alias_definition.match(text[end:])
                if alias_def is not None:
                    names = CaseAliasExtractor.short_names_from_match(alias_def)
                    alias_name = names[0] if names else None
                    end += alias_def.end()
                if alias_name is None:
                    alias_name = CaseReferenceExtractor.matching_alias_name(title, aliases)
            else:
                attached = self.alias_after_of(tail, aliases)
                if attached is not None:
                    alias_name, consumed = attached
                    end += consumed

            kind = (
                CitationKind.RULE
                if match.group("kind").lower().startswith("r")
                else CitationKind.PROVISION
            )
            spans.append(
                CandidateSpan(
                    start=match.start(),
                    end=end,
                    kind=kind,
                    quoted_text=text[match.start() : end].strip(),
                    alias_short_name=alias_name,
                    title=title,
                    neutral_citation=None,
                    slr_citation=None,
                    edition=edition,
                    provision_citations=CaseReferenceExtractor.parse_provision_refs(
                        match.group("kind"),
                        match.group("refs"),
                    ),
                    paragraph_pins=(),
                )
            )
        return spans

    def self_spans(self, text: str) -> list[CandidateSpan]:
        spans: list[CandidateSpan] = []
        for pattern in (self.above_pin, self.described_at):
            for match in pattern.finditer(text):
                pins = CaseReferenceExtractor.parse_pins(match.group("pins"))
                spans.append(
                    CandidateSpan(
                        start=match.start(),
                        end=match.end(),
                        kind=CitationKind.SELF,
                        quoted_text=match.group(0).strip(),
                        alias_short_name=None,
                        title=None,
                        neutral_citation=None,
                        slr_citation=None,
                        edition=None,
                        provision_citations=(),
                        paragraph_pins=pins,
                    )
                )
        return spans

    def book_spans(self, text: str) -> list[CandidateSpan]:
        spans: list[CandidateSpan] = []
        for match in self.book.finditer(text):
            spans.append(
                CandidateSpan(
                    start=match.start(),
                    end=match.end(),
                    kind=CitationKind.BOOK,
                    quoted_text=match.group(0).strip(),
                    alias_short_name=None,
                    title=match.group("title").strip(),
                    neutral_citation=None,
                    slr_citation=None,
                    edition=None,
                    provision_citations=(),
                    paragraph_pins=(),
                )
            )
        return spans

    def re_spans(self, text: str) -> list[CandidateSpan]:
        spans: list[CandidateSpan] = []
        for match in self.re_case.finditer(text):
            trailing = self.after_re.match(text[match.end() :])
            end = match.end()
            pins: tuple[int, ...] = ()
            if trailing is not None and trailing.group("pins"):
                end += trailing.end()
                pins = CaseReferenceExtractor.parse_pins(trailing.group("pins"))
            spans.append(
                CandidateSpan(
                    start=match.start(),
                    end=end,
                    kind=CitationKind.CASE,
                    quoted_text=text[match.start() : end].strip(),
                    alias_short_name=None,
                    title=match.group(0),
                    neutral_citation=None,
                    slr_citation=None,
                    edition=None,
                    provision_citations=(),
                    paragraph_pins=pins,
                )
            )
        return spans

    def alias_mention_spans(self, text: str, aliases: list[ExtractedAlias]) -> list[CandidateSpan]:
        definitions = [match.span() for match in self.aliases.alias_definition.finditer(text)]
        after_alias = re.compile(
            rf"^(?P<pins>\s+at\s+{self.pin_cluster})?",
            re.IGNORECASE,
        )
        spans: list[CandidateSpan] = []
        for alias in sorted(aliases, key=lambda item: -len(item.short_name)):
            if alias.kind == AliasKind.FACT:
                continue
            pattern = re.compile(rf"(?<!\w){re.escape(alias.short_name)}(?!\w)")
            for match in pattern.finditer(text):
                if CaseReferenceExtractor.is_occupied(match.start(), match.end(), definitions):
                    continue
                trailing = after_alias.match(text[match.end() :])
                end = match.end()
                pins: tuple[int, ...] = ()
                if trailing is not None and trailing.group("pins"):
                    end += trailing.end()
                    pins = CaseReferenceExtractor.parse_pins(trailing.group("pins"))
                kind = CaseReferenceExtractor.kind_for_alias(alias.kind)
                if kind is None:
                    continue
                neutral, slr = self.citations_from_text(alias.expanded_text)
                spans.append(
                    CandidateSpan(
                        start=match.start(),
                        end=end,
                        kind=kind,
                        quoted_text=text[match.start() : end].strip(),
                        alias_short_name=alias.short_name,
                        title=alias.title,
                        neutral_citation=neutral,
                        slr_citation=slr,
                        edition=alias.edition,
                        provision_citations=(),
                        paragraph_pins=pins,
                    )
                )
        return spans

    def anaphoric_pins(self, text: str, state: DiscourseState) -> list[CandidateSpan]:
        spans: list[CandidateSpan] = []
        for match in self.bare_pin.finditer(text):
            pins = CaseReferenceExtractor.parse_pins(match.group("pins"))
            if state.current_case is not None:
                spans.append(
                    CandidateSpan(
                        start=match.start(),
                        end=match.end(),
                        kind=CitationKind.CASE,
                        quoted_text=match.group(0).strip(),
                        alias_short_name=state.current_case,
                        title=None,
                        neutral_citation=None,
                        slr_citation=None,
                        edition=None,
                        provision_citations=(),
                        paragraph_pins=pins,
                    )
                )
                continue
            spans.append(
                CandidateSpan(
                    start=match.start(),
                    end=match.end(),
                    kind=CitationKind.SELF,
                    quoted_text=match.group(0).strip(),
                    alias_short_name=None,
                    title=None,
                    neutral_citation=None,
                    slr_citation=None,
                    edition=None,
                    provision_citations=(),
                    paragraph_pins=pins,
                )
            )
        return spans

    def reporter_matches(self, text: str) -> list[re.Match[str]]:
        found: dict[tuple[int, int], re.Match[str]] = {}
        for pattern in (
            self.aliases.neutral_citation,
            self.aliases.slr_citation,
            self.aliases.other_reporter,
        ):
            for match in pattern.finditer(text):
                found[(match.start(), match.end())] = match
        return sorted(found.values(), key=lambda item: item.start())

    def title_start(self, text: str, cite_start: int) -> int:
        title = self.aliases.title_before_citation(text[:cite_start], cite_start)
        if not title:
            return cite_start
        index = text.rfind(title, max(0, cite_start - 220), cite_start)
        return index if index != -1 else cite_start

    def alias_after_of(
        self,
        tail: str,
        aliases: list[ExtractedAlias],
    ) -> tuple[str, int] | None:
        prefix = self.of_the.match(tail)
        if prefix is None:
            return None
        after = tail[prefix.end() :]
        for alias in sorted(aliases, key=lambda item: -len(item.short_name)):
            if alias.kind not in {AliasKind.ACT, AliasKind.RULES}:
                continue
            if not after.startswith(alias.short_name):
                continue
            remainder = after[len(alias.short_name) :]
            if remainder and self.word_boundary_end.match(remainder[0]) is not None:
                continue
            return alias.short_name, prefix.end() + len(alias.short_name)
        return None

    @staticmethod
    def matching_alias_name(title: str, aliases: list[ExtractedAlias]) -> str | None:
        for alias in aliases:
            if alias.short_name == title:
                return alias.short_name
        return None

    def citations_from_text(self, text: str) -> tuple[str | None, str | None]:
        neutral = self.aliases.neutral_citation.search(text)
        slr = self.aliases.slr_citation.search(text)
        return (
            neutral.group(0) if neutral is not None else None,
            slr.group(0) if slr is not None else None,
        )

    @staticmethod
    def with_current_instrument(span: CandidateSpan, state: DiscourseState) -> CandidateSpan:
        if span.kind == CitationKind.PROVISION and span.alias_short_name is None and span.title is None:
            if state.current_act is None:
                return span
            return CandidateSpan(
                start=span.start,
                end=span.end,
                kind=span.kind,
                quoted_text=span.quoted_text,
                alias_short_name=state.current_act,
                title=span.title,
                neutral_citation=span.neutral_citation,
                slr_citation=span.slr_citation,
                edition=span.edition,
                provision_citations=span.provision_citations,
                paragraph_pins=span.paragraph_pins,
            )
        if span.kind == CitationKind.RULE and span.alias_short_name is None and span.title is None:
            if state.current_rules is None:
                return span
            return CandidateSpan(
                start=span.start,
                end=span.end,
                kind=span.kind,
                quoted_text=span.quoted_text,
                alias_short_name=state.current_rules,
                title=span.title,
                neutral_citation=span.neutral_citation,
                slr_citation=span.slr_citation,
                edition=span.edition,
                provision_citations=span.provision_citations,
                paragraph_pins=span.paragraph_pins,
            )
        return span

    @staticmethod
    def update_state(state: DiscourseState, span: CandidateSpan) -> None:
        name = span.alias_short_name or span.title
        if span.kind == CitationKind.CASE:
            if name is not None:
                state.current_case = name
            return
        if span.kind == CitationKind.ACT and name is not None:
            state.current_act = name
            return
        if span.kind == CitationKind.PROVISION and name is not None:
            state.current_act = name
            return
        if span.kind == CitationKind.RULE and name is not None:
            state.current_rules = name

    @staticmethod
    def kind_for_alias(kind: AliasKind) -> CitationKind | None:
        if kind == AliasKind.CASE:
            return CitationKind.CASE
        if kind == AliasKind.ACT:
            return CitationKind.ACT
        if kind == AliasKind.RULES:
            return CitationKind.RULE
        return None

    @staticmethod
    def aliases_from_rows(rows: list[Alias]) -> list[ExtractedAlias]:
        extracted: list[ExtractedAlias] = []
        for row in rows:
            extracted.append(
                ExtractedAlias(
                    short_name=row.short_name,
                    expanded_text=row.expanded_text,
                    kind=CaseAliasExtractor.classify_kind(row.expanded_text),
                    title=None,
                    neutral_citation=None,
                    slr_citation=None,
                    edition=None,
                )
            )
        return extracted

    @staticmethod
    def is_occupied(start: int, end: int, occupied: list[tuple[int, int]]) -> bool:
        for left, right in occupied:
            if start < right and end > left:
                return True
        return False

    @staticmethod
    def parse_pins(text: str) -> tuple[int, ...]:
        pins: list[int] = []
        for match in re.finditer(r"\[(\d+)\](?:\s*[–\-—]\s*\[(\d+)\])?", text):
            start = int(match.group(1))
            end = int(match.group(2)) if match.group(2) else start
            if end < start:
                start, end = end, start
            if end - start > 80:
                continue
            pins.extend(range(start, end + 1))
        return tuple(pins)

    @staticmethod
    def parse_provision_refs(kind: str, refs: str) -> tuple[str, ...]:
        prefix = "r" if kind.lower().startswith("r") else "s"
        parts = re.split(r"\s*(?:,|and)\s*", refs)
        citations: list[str] = []
        for part in parts:
            cleaned = re.sub(r"\s+", "", part)
            if cleaned:
                citations.append(f"{prefix} {cleaned}")
        return tuple(citations)

    @staticmethod
    def first_neutral_citation(text: str) -> str | None:
        match = re.search(
            r"\[[12]\d{3}\]\s+SG(?:HC(?:[RF]|\([A-Z]+\))?|CA(?:\([A-Z]+\))?|DC|FC)\s+\d+",
            text,
        )
        return match.group(0) if match is not None else None
