from collections import Counter
from collections.abc import Sequence

from bs4 import BeautifulSoup, Tag

from src.cases.schema import DocumentParse, ExtractedParagraph, ScrapeLimits
from src.database import get_raw_source_session_maker
from src.logger import get_logger
from src.notify.schema import FailureItem, StageReport, StageReporter, emit_report, stored_failures
from src.raw.models.cases import CaseDocumentLayout, ParseStatus
from src.raw.repository import RawCaseRepository

NUMBERED_LAYOUTS = frozenset(
    {CaseDocumentLayout.MODERN_JUDG1, CaseDocumentLayout.NUMBERED_PLAIN_P}
)
JUDG1_CLASSES = frozenset({"Judg-1", "Judg-1-firstpara", "judg-1"})
HEADING_PREFIXES = (
    "Judg-Heading",
    "judg-heading",
    "Judg-Author",
    "Judg-author",
    "Judg-Hearing",
    "Judg-Date",
)
MIN_UNNUMBERED_WORDS = 10

logger = get_logger(__name__)


class CaseDocumentParser:
    def __init__(self, repository: RawCaseRepository) -> None:
        self.repository = repository

    def parse_pending(
        self,
        max_documents: int | None,
        reporter: StageReporter | None = None,
        limits: ScrapeLimits | None = None,
    ) -> StageReport:
        persist_every = (limits or ScrapeLimits()).parse_persist_every
        pending = self.repository.get_documents_pending_parse(max_documents)
        logger.info("Parsing %s pending documents", len(pending))

        counts: Counter[ParseStatus] = Counter()
        failures: list[FailureItem] = []
        for parsed, (raw_case, document) in enumerate(pending, start=1):
            result = self.parse_html(document.html)
            self.repository.save_parse_result(raw_case, document, result)
            counts[result.parse_status] += 1
            self.log_parse(raw_case.neutral_citation, result)
            failure = self.parse_failure(raw_case.neutral_citation, result)
            if failure is not None:
                failures.append(failure)
            if parsed % persist_every == 0:
                self.repository.session.commit()
                emit_report(
                    reporter,
                    StageReport(
                        stage="parse",
                        counts={
                            "parsed": parsed,
                            "complete": counts[ParseStatus.COMPLETE],
                            "incomplete": counts[ParseStatus.INCOMPLETE],
                            "unknown layout": counts[ParseStatus.UNKNOWN_LAYOUT],
                            "failed": counts[ParseStatus.FAILED],
                        },
                        failures=stored_failures(failures),
                        in_progress=True,
                        done=parsed,
                        total=len(pending),
                    ),
                )

        logger.info(
            "Parsed %s documents: %s complete, %s incomplete, %s unknown_layout, %s failed",
            len(pending),
            counts[ParseStatus.COMPLETE],
            counts[ParseStatus.INCOMPLETE],
            counts[ParseStatus.UNKNOWN_LAYOUT],
            counts[ParseStatus.FAILED],
        )
        report = StageReport(
            stage="parse",
            counts={
                "parsed": len(pending),
                "complete": counts[ParseStatus.COMPLETE],
                "incomplete": counts[ParseStatus.INCOMPLETE],
                "unknown layout": counts[ParseStatus.UNKNOWN_LAYOUT],
                "failed": counts[ParseStatus.FAILED],
            },
            failures=stored_failures(failures),
        )

        emit_report(reporter, report)

        return report

    @staticmethod
    def log_parse(citation: str, result: DocumentParse) -> None:
        if result.parse_status == ParseStatus.INCOMPLETE:
            logger.warning(
                "Incomplete parse for %s (%s): expected=%s extracted=%s",
                citation,
                result.layout.value,
                result.expected_paragraph_count,
                result.extracted_paragraph_count,
            )
        elif result.parse_status == ParseStatus.UNKNOWN_LAYOUT:
            logger.warning("Unknown layout for %s", citation)
        elif result.parse_status == ParseStatus.FAILED:
            logger.warning("Parse failed for %s", citation)

    @staticmethod
    def parse_failure(citation: str, result: DocumentParse) -> FailureItem | None:
        if result.parse_status == ParseStatus.INCOMPLETE:
            return FailureItem(
                citation,
                (
                    f"incomplete ({result.layout.value}): "
                    f"expected={result.expected_paragraph_count} "
                    f"extracted={result.extracted_paragraph_count}"
                ),
            )
        if result.parse_status == ParseStatus.UNKNOWN_LAYOUT:
            return FailureItem(citation, "unknown layout")
        if result.parse_status == ParseStatus.FAILED:
            return FailureItem(citation, "parse failed")
        return None

    @staticmethod
    def parse_html(html: str | None) -> DocumentParse:
        if html is None or not html.strip():
            return DocumentParse.failed()

        body = CaseDocumentParser.judgment_body(html)
        layout = CaseDocumentParser.detect_layout(body)

        if layout == CaseDocumentLayout.MODERN_JUDG1:
            return CaseDocumentParser.parse_modern_judg1(body)
        if layout == CaseDocumentLayout.NUMBERED_PLAIN_P:
            return CaseDocumentParser.parse_numbered_plain_p(body)
        if layout == CaseDocumentLayout.UNNUMBERED_BR:
            extracted = len(CaseDocumentParser.blocks_from_break_paragraphs(body.find_all("p")))
            return DocumentParse.from_counts(layout, None, extracted, extracted > 0)

        return DocumentParse.unknown_layout()

    @staticmethod
    def preferred_html(candidates: list[tuple[int, str]]) -> str | None:
        best_html: str | None = None
        best_score: tuple[int, int] | None = None
        for path_rank, html in candidates:
            parsed = CaseDocumentParser.parse_html(html)
            if parsed.parse_status == ParseStatus.COMPLETE and parsed.layout in NUMBERED_LAYOUTS:
                quality = 3
            elif parsed.layout in NUMBERED_LAYOUTS:
                quality = 2
            elif parsed.parse_status == ParseStatus.COMPLETE:
                quality = 1
            else:
                quality = 0
            score = (quality, -path_rank)
            if best_score is None or score > best_score:
                best_score = score
                best_html = html
        return best_html

    @staticmethod
    def detect_layout(body: Tag) -> CaseDocumentLayout:
        paragraphs = body.find_all("p")
        numbered_starts = 0

        for paragraph in paragraphs:
            if CaseDocumentParser.has_judg1_class(paragraph):
                return CaseDocumentLayout.MODERN_JUDG1
            if CaseDocumentParser.leading_paragraph_number(paragraph.get_text(" ", strip=True)) is not None:
                numbered_starts += 1

        if numbered_starts >= 2:
            return CaseDocumentLayout.NUMBERED_PLAIN_P
        if len(CaseDocumentParser.blocks_from_break_paragraphs(paragraphs)) >= 2:
            return CaseDocumentLayout.UNNUMBERED_BR
        return CaseDocumentLayout.UNKNOWN

    @staticmethod
    def parse_modern_judg1(body: Tag) -> DocumentParse:
        numbers: list[int] = []
        for paragraph in body.find_all("p"):
            if not CaseDocumentParser.has_judg1_class(paragraph):
                continue
            number = CaseDocumentParser.leading_paragraph_number(paragraph.get_text(" ", strip=True))
            if number is not None:
                numbers.append(number)
        return CaseDocumentParser.numbered_result(CaseDocumentLayout.MODERN_JUDG1, numbers)

    @staticmethod
    def parse_numbered_plain_p(body: Tag) -> DocumentParse:
        numbers: list[int] = []
        for paragraph in body.find_all("p"):
            number = CaseDocumentParser.leading_paragraph_number(paragraph.get_text(" ", strip=True))
            if number is not None:
                numbers.append(number)
        return CaseDocumentParser.numbered_result(CaseDocumentLayout.NUMBERED_PLAIN_P, numbers)

    @staticmethod
    def numbered_result(layout: CaseDocumentLayout, numbers: list[int]) -> DocumentParse:
        if not numbers:
            return DocumentParse.from_counts(layout, None, 0, False)

        expected = max(numbers)
        extracted = len(numbers)
        complete = extracted == expected and set(numbers) == set(range(1, expected + 1))
        return DocumentParse.from_counts(layout, expected, extracted, complete)

    @staticmethod
    def has_judg1_class(paragraph: Tag) -> bool:
        classes = paragraph.get("class")
        if not isinstance(classes, list):
            return False
        return bool(JUDG1_CLASSES.intersection(str(item) for item in classes))

    @staticmethod
    def leading_paragraph_number(text: str) -> int | None:
        if not text:
            return None
        token = text.split(maxsplit=1)[0].rstrip(".)")
        if token.isdigit():
            return int(token)
        return None

    def extract_paragraphs(self, html: str, layout: CaseDocumentLayout) -> list[ExtractedParagraph]:
        body = CaseDocumentParser.judgment_body(html)
        if layout == CaseDocumentLayout.MODERN_JUDG1:
            return self.extract_modern_judg1(body)
        if layout == CaseDocumentLayout.NUMBERED_PLAIN_P:
            return self.extract_numbered_plain_p(body)
        if layout == CaseDocumentLayout.UNNUMBERED_BR:
            return [
                ExtractedParagraph(ordinal=index, content=text)
                for index, text in enumerate(
                    self.blocks_from_break_paragraphs(body.find_all("p")),
                    start=1,
                )
            ]
        return []

    def extract_modern_judg1(self, body: Tag) -> list[ExtractedParagraph]:
        blocks: list[list[str]] = []
        for paragraph in body.find_all("p"):
            text = paragraph.get_text(" ", strip=True)
            if self.has_judg1_class(paragraph):
                number = self.leading_paragraph_number(text)
                if number is not None:
                    blocks.append([self.text_after_leading_number(text)])
                    continue
            if blocks and text and not self.is_heading_paragraph(paragraph):
                blocks[-1].append(text)
        return CaseDocumentParser.paragraphs_from_blocks(blocks)

    def extract_numbered_plain_p(self, body: Tag) -> list[ExtractedParagraph]:
        blocks: list[list[str]] = []
        for paragraph in body.find_all("p"):
            text = paragraph.get_text(" ", strip=True)
            number = self.leading_paragraph_number(text)
            if number is not None:
                blocks.append([self.text_after_leading_number(text)])
                continue
            if blocks and text:
                blocks[-1].append(text)
        return CaseDocumentParser.paragraphs_from_blocks(blocks)

    @staticmethod
    def judgment_body(html: str) -> Tag:
        soup = BeautifulSoup(html, "lxml")
        found = soup.find(id="judgments")
        return found if isinstance(found, Tag) else soup

    @staticmethod
    def paragraphs_from_blocks(blocks: list[list[str]]) -> list[ExtractedParagraph]:
        return [
            ExtractedParagraph(ordinal=index, content="\n".join(part for part in parts if part))
            for index, parts in enumerate(blocks, start=1)
        ]

    @staticmethod
    def text_after_leading_number(text: str) -> str:
        parts = text.split(maxsplit=1)
        return parts[1] if len(parts) >= 2 else ""

    @staticmethod
    def is_heading_paragraph(paragraph: Tag) -> bool:
        classes = paragraph.get("class")
        if not isinstance(classes, list):
            return False
        return any(str(item).startswith(HEADING_PREFIXES) for item in classes)

    @staticmethod
    def blocks_from_break_paragraphs(paragraphs: Sequence[Tag]) -> list[str]:
        blocks: list[str] = []
        for paragraph in paragraphs:
            markup = str(paragraph).replace("<br/>", "<br>").replace("<br />", "<br>")
            for piece in markup.split("<br><br>"):
                text = BeautifulSoup(piece, "lxml").get_text(" ", strip=True)
                if len(text.split()) >= MIN_UNNUMBERED_WORDS:
                    blocks.append(text)
        return blocks


class CaseRawParser:
    @staticmethod
    def run(
        max_documents: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        session_maker = get_raw_source_session_maker()
        with session_maker() as session:
            parsed = CaseDocumentParser(RawCaseRepository(session)).parse_pending(
                max_documents,
                reporter,
                ScrapeLimits(),
            )
            session.commit()

        logger.info("Raw parse finished: %s documents parsed", parsed.counts.get("parsed", 0))
        return parsed
