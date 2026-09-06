import re
from re import Pattern

from src.aliases.schema import AliasKind, ExtractedAlias
from src.database import get_knowledge_base_session_maker
from src.knowledge.models.paragraphs import Paragraph
from src.knowledge.repository import KnowledgeCaseRepository
from src.logger import get_logger
from src.notify.schema import StageReport, StageReporter, emit_report

logger = get_logger(__name__)


class CaseAliasExtractor:
    def __init__(self) -> None:
        self.alias_definition: Pattern[str] = re.compile(
            r"\("
            r"(?:the\s+)?"
            r'["“]\s*([^"”]+?)\s*["”]'
            r"(?:\s+or\s+(?:the\s+)?"
            r'["“]\s*([^"”]+?)\s*["”]'
            r")?"
            r"\)"
        )
        self.neutral_citation: Pattern[str] = re.compile(
            r"\[[12]\d{3}\]\s+SG(?:HC(?:[RF]|\([A-Z]+\))?|CA(?:\([A-Z]+\))?|DC|FC)\s+\d+"
        )
        self.slr_citation: Pattern[str] = re.compile(
            r"\[[12]\d{3}\]\s+\d+\s+SLR(?:\(R\))?\s+\d+"
        )
        self.other_reporter: Pattern[str] = re.compile(
            r"\[[12]\d{3}\]\s+\d+\s+[A-Z][A-Za-z.]+\s+\d+"
        )
        self.instrument: Pattern[str] = re.compile(
            r"(?:the\s+)?"
            r"(?P<title>"
            r"[A-Z][\w'’\-,]*(?:\s+[A-Z][\w'’\-,]+|\s+and|\s+of|\s+the|\s+for|\s+\([^)]+\))*"
            r"\s+(?:Act|Rules|Regulations|Code|Order)"
            r"(?:\s+\d{4})?"
            r")"
            r"(?:\s+\((?P<edition>Cap\.?\s+[^)]+|[^)]*Rev Ed)\))?"
            r"\s*$"
        )
        self.title_delimiter: Pattern[str] = re.compile(
            r"(?:(?i:see(?:,\s*e\.?g\.?,?)?)\s+|"
            r"(?i:in the decision of(?: the [^.]+)? in)\s+|"
            r"(?i:in)\s+(?=[A-Z]))"
        )
        self.capital_phrase: Pattern[str] = re.compile(
            r"([A-Z][A-Za-z0-9'’\-]*(?:\s+[A-Za-z0-9'’\-]+){0,12})\s*$"
        )

    def run(
        self,
        max_cases: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        session_maker = get_knowledge_base_session_maker()
        with session_maker() as session:
            repository = KnowledgeCaseRepository(session)
            cases = repository.get_cases_pending_citation_extraction(max_cases)
            total = 0

            for case, paragraphs in cases:
                extracted = self.extract_case(paragraphs)
                repository.replace_aliases(case.id, extracted)
                session.commit()
                total += len(extracted)
                logger.info("Aliases for %s: %s", case.neutral_citation, len(extracted))

        logger.info(
            "Alias extraction finished: %s aliases across %s cases",
            total,
            len(cases),
        )
        report = StageReport(stage="aliases", counts={"cases": len(cases), "aliases": total})

        emit_report(reporter, report)

        return report

    def extract_case(self, paragraphs: list[Paragraph]) -> list[ExtractedAlias]:
        seen: dict[str, ExtractedAlias] = {}
        for paragraph in paragraphs:
            for item in self.extract_from_text(paragraph.content):
                if item.short_name not in seen:
                    seen[item.short_name] = item
        return list(seen.values())

    def extract_from_text(self, text: str) -> list[ExtractedAlias]:
        extracted: list[ExtractedAlias] = []
        for match in self.alias_definition.finditer(text):
            preceding = text[: match.start()].rstrip()
            expanded_text, title, neutral_citation, slr_citation, edition = (
                self.expansion_from_preceding(preceding)
            )
            kind = CaseAliasExtractor.classify_kind(expanded_text)
            for short_name in CaseAliasExtractor.short_names_from_match(match):
                extracted.append(
                    ExtractedAlias(
                        short_name=short_name,
                        expanded_text=expanded_text,
                        kind=kind,
                        title=title,
                        neutral_citation=neutral_citation,
                        slr_citation=slr_citation,
                        edition=edition,
                    )
                )
        return extracted

    def expansion_from_preceding(
        self,
        preceding: str,
    ) -> tuple[str, str | None, str | None, str | None, str | None]:
        reporter = self.last_reporter(preceding)
        if reporter is not None and reporter.end() >= len(preceding.rstrip()) - 1:
            title = self.title_before_citation(preceding, reporter.start())
            citation = reporter.group(0)
            expanded = f"{title} {citation}".strip() if title else citation
            neutral = citation if self.neutral_citation.fullmatch(citation) else None
            slr = citation if self.slr_citation.fullmatch(citation) else None
            return expanded, title or None, neutral, slr, None

        instrument = self.instrument.search(preceding)
        if instrument is not None:
            title = instrument.group("title").strip()
            edition = instrument.group("edition")
            expanded = title if edition is None else f"{title} ({edition})"
            return expanded, title, None, None, edition

        phrase = self.capital_phrase.search(preceding.rstrip(" ,;"))
        if phrase is not None:
            expanded = phrase.group(1).strip()
            return expanded, expanded, None, None, None

        fallback = preceding[-80:].strip(" ,;(") or preceding
        return fallback, None, None, None, None

    def last_reporter(self, text: str) -> re.Match[str] | None:
        found: re.Match[str] | None = None
        for pattern in (self.neutral_citation, self.slr_citation, self.other_reporter):
            for match in pattern.finditer(text):
                if found is None or match.end() > found.end():
                    found = match
        return found

    def title_before_citation(self, preceding: str, cite_start: int) -> str:
        window = preceding[max(0, cite_start - 220) : cite_start]
        earlier = self.last_reporter(window)
        if earlier is not None:
            window = window[earlier.end() :]
        wrapper = CaseAliasExtractor.last_unclosed_paren(window)
        if wrapper is not None:
            window = window[wrapper + 1 :]
        delimiter = None
        for match in self.title_delimiter.finditer(window):
            delimiter = match
        if delimiter is not None:
            window = window[delimiter.end() :]
        start = re.search(r"(?:Re\s+)?[A-Z]", window)
        if start is None:
            return window.strip(" ,;(")
        return window[start.start() :].strip(" ,;(")

    @staticmethod
    def last_unclosed_paren(text: str) -> int | None:
        depth = 0
        for index in range(len(text) - 1, -1, -1):
            character = text[index]
            if character == ")":
                depth += 1
                continue
            if character != "(":
                continue
            if depth == 0:
                return index
            depth -= 1
        return None

    @staticmethod
    def short_names_from_match(match: re.Match[str]) -> list[str]:
        names: list[str] = []
        for group in match.groups():
            if not group:
                continue
            short_name = re.sub(r"\s+", " ", group).strip()
            if short_name:
                names.append(short_name)
        return names

    @staticmethod
    def classify_kind(expanded_text: str) -> AliasKind:
        if re.search(r"\bRules\b", expanded_text) or re.search(r"\bR\d+\b", expanded_text):
            return AliasKind.RULES
        if (
            re.search(r"\bAct\b", expanded_text)
            or "Rev Ed" in expanded_text
            or re.search(r"\bCap\.?\b", expanded_text)
        ):
            return AliasKind.ACT
        if (
            re.search(r"\[[12]\d{3}\]", expanded_text)
            or re.search(r"\sv\s", expanded_text)
            or re.match(r"Re\s+", expanded_text) is not None
        ):
            return AliasKind.CASE
        return AliasKind.FACT
