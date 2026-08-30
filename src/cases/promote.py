import re

from src.cases.client import LawNetClient
from src.cases.parse import CaseDocumentParser
from src.database import get_knowledge_base_session_maker, get_raw_source_session_maker
from src.knowledge.repository import KnowledgeCaseRepository
from src.logger import get_logger
from src.raw.models.cases import RawCase, RawCaseDocument
from src.raw.repository import RawCaseRepository

COURT_NAMES = {
    "SGCA": "Court of Appeal",
    "SGCA(I)": "Court of Appeal (International)",
    "SGHC": "General Division of the High Court",
    "SGHC(A)": "Appellate Division of the High Court",
    "SGHC(I)": "Singapore International Commercial Court",
    "SGHCF": "Family Division of the High Court",
    "SGHCR": "High Court Registrar",
}

JUDGE_TITLES = frozenset({"J", "IJ", "CJ", "JCA", "JAD", "JC", "SJ", "JA"})
CITATION_COURT = re.compile(r"\]\s+(.+?)\s+\d+\s*$")
COUNSEL_REPRESENTS = re.compile(r"\s+for(?:\s+the)?\s+(.+?)\.?$", re.IGNORECASE)
COUNSEL_APPEARANCE_SPLIT = re.compile(r"\.\s+(?=[A-Z].+\s+for\s+)", re.IGNORECASE)
COUNSEL_HAS_SIDE = re.compile(r"\s+for(?:\s+the)?\s+", re.IGNORECASE)
COUNSEL_ABSENCE = re.compile(r"\b(absent|unrepresented|in person)\b", re.IGNORECASE)
COUNSEL_NAME_SPLIT = re.compile(
    r",\s*(?:and|with)\s+|\s+(?:and|with)\s+|,\s*",
    re.IGNORECASE,
)
COUNSEL_LEADING_JOINER = re.compile(r"^(?:and|with)\s+", re.IGNORECASE)
COUNSEL_POST_NOMINALS = frozenset({"SC", "QC", "PC"})
FIRM_PARENTHETICAL = re.compile(
    r"\b(LLP|LLC|Pte|Ltd|Partners(?:hip)?|Chambers|Law Corporation|"
    r"Attorney-?General|Attorney General|Solicitor-?General|Solicitor General|"
    r"Director of|instructed|instructing)\b|"
    r"&|and Co",
    re.IGNORECASE,
)
SKIP_COUNSEL_NAMES = frozenset({"and", "with", "instructed", "instructing", "absent", "unrepresented", "the"})
PARTY_WORDS = ("respondent", "defendant", "appellant", "plaintiff", "claimant")

logger = get_logger(__name__)


class CasePromoter:
    def __init__(
        self,
        raw_repository: RawCaseRepository,
        knowledge_repository: KnowledgeCaseRepository,
        parser: CaseDocumentParser,
    ) -> None:
        self.raw_repository = raw_repository
        self.knowledge_repository = knowledge_repository
        self.parser = parser

    def promote_pending(self, max_cases: int | None) -> int:
        pending = self.raw_repository.get_documents_pending_promote(max_cases)
        pending_citations = [raw_case.neutral_citation for raw_case, _ in pending]

        already_promoted = self.knowledge_repository.get_existing_citations(pending_citations)

        logger.info(
            "Promoting %s complete cases (%s already in knowledge base)",
            len(pending),
            len(already_promoted),
        )

        promoted = 0
        skipped = 0
        for raw_case, document in pending:
            if raw_case.neutral_citation in already_promoted:
                self.raw_repository.mark_promoted(document)
                skipped += 1
                continue
            if not self.promote_one(raw_case, document):
                skipped += 1
                continue
            self.raw_repository.mark_promoted(document)
            promoted += 1

        logger.info("Promoted %s cases, skipped %s", promoted, skipped)
        return promoted

    def promote_one(self, raw_case: RawCase, document: RawCaseDocument) -> bool:
        citation = raw_case.neutral_citation
        if document.html is None or document.layout is None:
            logger.warning("Cannot promote %s: missing html or layout", citation)
            return False

        metadata = document.source_metadata or {}
        search = raw_case.search_result or {}
        decision_date = raw_case.date or LawNetClient.parse_iso_date(metadata.get("DecisionDate"))
        if decision_date is None:
            logger.warning("Cannot promote %s: no decision date", citation)
            return False
        
        title = CasePromoter.case_title(metadata, search)
        if title is None:
            logger.warning("Cannot promote %s: no title", citation)
            return False

        paragraphs = self.parser.extract_paragraphs(document.html, document.layout)
        if len(paragraphs) != document.extracted_paragraph_count:
            logger.warning(
                "Cannot promote %s: extract count %s != stored %s",
                citation,
                len(paragraphs),
                document.extracted_paragraph_count,
            )
            return False

        court_code = CasePromoter.court_code(citation, metadata)
        if court_code is None:
            logger.warning("Cannot promote %s: no court code", citation)
            return False

        court = self.knowledge_repository.get_or_create_court(
            court_code,
            COURT_NAMES.get(court_code, court_code),
        )
        case = self.knowledge_repository.add_case(
            court_id=court.id,
            decision_date=decision_date,
            uri=document.source_url,
            title=title,
            neutral_citation=citation,
            case_number=CasePromoter.case_number(metadata, search),
        )
        
        self.knowledge_repository.add_paragraphs(
            case.id,
            case.uri,
            [(paragraph.ordinal, paragraph.content) for paragraph in paragraphs],
        )

        for full_name, title_abbrev in CasePromoter.judges(metadata, search):
            judge = self.knowledge_repository.get_or_create_judge(full_name, title_abbrev)
            self.knowledge_repository.add_case_judge(case.id, judge.id)
        for name, role in CasePromoter.parties(metadata):
            party = self.knowledge_repository.get_or_create_party(name)
            self.knowledge_repository.add_case_party(case.id, party.id, role)
        for name, represents in CasePromoter.counsels(metadata):
            counsel = self.knowledge_repository.get_or_create_counsel(name)
            self.knowledge_repository.add_case_counsel(case.id, counsel.id, represents)

        return True

    @staticmethod
    def text(value: object) -> str | None:
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())
        return None

    @staticmethod
    def content(value: object) -> str | None:
        text = CasePromoter.text(value)
        if text is not None:
            return text
        if isinstance(value, dict):
            return CasePromoter.text(value.get("content"))
        return None

    @staticmethod
    def text_values(value: object) -> list[str]:
        if isinstance(value, list):
            names: list[str] = []
            for item in value:
                if isinstance(item, list):
                    continue
                names.extend(CasePromoter.text_values(item))
            return names
        text = CasePromoter.content(value)
        return [text] if text is not None else []

    @staticmethod
    def court_code(citation: str, metadata: dict[str, object]) -> str | None:
        citation_meta = metadata.get("NeutralCitation")
        if isinstance(citation_meta, dict):
            court = CasePromoter.text(citation_meta.get("Court"))
            if court is not None:
                return court
        match = CITATION_COURT.search(citation)
        return match.group(1) if match is not None else None

    @staticmethod
    def case_title(metadata: dict[str, object], search: dict[str, object]) -> str | None:
        title = CasePromoter.text(metadata.get("CaseTitle"))
        if title is not None:
            return title
        titles = search.get("titles")
        if isinstance(titles, list) and titles:
            return CasePromoter.text(titles[0])
        return None

    @staticmethod
    def case_number(metadata: dict[str, object], search: dict[str, object]) -> str | None:
        return CasePromoter.text(metadata.get("CaseNumber")) or CasePromoter.text(search.get("casenumber"))

    @staticmethod
    def judges(
        metadata: dict[str, object],
        search: dict[str, object],
    ) -> list[tuple[str, str | None]]:
        raw = CasePromoter.coram_values(metadata.get("Corams"))
        if not raw:
            corams = search.get("corams")
            if isinstance(corams, list):
                raw = [item for item in corams if isinstance(item, str)]

        result: list[tuple[str, str | None]] = []
        for item in raw:
            parts = item.split()
            if len(parts) >= 2 and parts[-1] in JUDGE_TITLES:
                result.append((" ".join(parts[:-1]), parts[-1]))
            else:
                result.append((item, None))
        return result

    @staticmethod
    def coram_values(corams: object) -> list[str]:
        if not isinstance(corams, dict):
            return []
        return CasePromoter.text_values(corams.get("Coram"))

    @staticmethod
    def parties(metadata: dict[str, object]) -> list[tuple[str, str | None]]:
        container = metadata.get("Parties")
        if not isinstance(container, dict):
            return []

        value = container.get("Party")
        items = value if isinstance(value, list) else [value]
        result: list[tuple[str, str | None]] = []
        for item in items:
            if isinstance(item, str):
                name = CasePromoter.text(item)
                if name is not None:
                    result.append((name, None))
            elif isinstance(item, dict):
                result.extend(CasePromoter.party_from_object(item))
        return result

    @staticmethod
    def party_from_object(party: dict[str, object]) -> list[tuple[str, str | None]]:
        role_text = CasePromoter.content(party.get("Role"))
        name = party.get("Name") or party.get("Party")
        if isinstance(name, str):
            cleaned = CasePromoter.text(name)
            return [(cleaned, role_text)] if cleaned is not None else []
        if isinstance(name, list):
            result: list[tuple[str, str | None]] = []
            for item in name:
                cleaned = CasePromoter.text(item)
                if cleaned is not None:
                    result.append((cleaned, role_text))
            return result
        return []

    @staticmethod
    def counsels(metadata: dict[str, object]) -> list[tuple[str, str | None]]:
        container = metadata.get("Counsels")
        if not isinstance(container, dict):
            return []

        result: list[tuple[str, str | None]] = []
        for line in CasePromoter.counsel_appearance_lines(container.get("Counsel")):
            if not CasePromoter.is_counsel_appearance_line(line):
                continue
            represents, names = CasePromoter.counsel_names_from_line(line)
            for name in names:
                result.append((name, represents))
        return result

    @staticmethod
    def counsel_appearance_lines(value: object) -> list[str]:
        items = value if isinstance(value, list) else [value]
        lines: list[str] = []
        for item in items:
            if not isinstance(item, str) or not item.strip():
                continue
            for part in COUNSEL_APPEARANCE_SPLIT.split(item.strip()):
                cleaned = part.strip(" .")
                if cleaned:
                    lines.append(cleaned)
        return lines

    @staticmethod
    def is_counsel_appearance_line(line: str) -> bool:
        return COUNSEL_HAS_SIDE.search(line) is not None and COUNSEL_ABSENCE.search(line) is None

    @staticmethod
    def counsel_names_from_line(line: str) -> tuple[str | None, list[str]]:
        match = COUNSEL_REPRESENTS.search(line)
        represents = None
        body = line
        if match is not None:
            represents = CasePromoter.text(match.group(1).rstrip("."))
            body = line[: match.start()]

        body = COUNSEL_LEADING_JOINER.sub("", body.strip())
        body = CasePromoter.strip_firm_parentheticals(body)
        chunks = CasePromoter.split_counsel_name_chunks(body)
        names = CasePromoter.merge_counsel_post_nominals(chunks)
        return represents, [name for name in names if CasePromoter.is_counsel_person_name(name)]

    @staticmethod
    def split_counsel_name_chunks(body: str) -> list[str]:
        chunks: list[str] = []
        start = 0
        depth = 0
        index = 0
        while index < len(body):
            char = body[index]
            if char == "(":
                depth += 1
                index += 1
                continue
            if char == ")":
                depth = max(0, depth - 1)
                index += 1
                continue
            if depth == 0:
                match = COUNSEL_NAME_SPLIT.match(body, index)
                if match is not None:
                    chunk = COUNSEL_LEADING_JOINER.sub("", body[start:index].strip(" ."))
                    if chunk:
                        chunks.extend(CasePromoter.split_ampersand_counsel_names(chunk))
                    index = match.end()
                    start = index
                    continue
            index += 1
        chunk = COUNSEL_LEADING_JOINER.sub("", body[start:].strip(" ."))
        if chunk:
            chunks.extend(CasePromoter.split_ampersand_counsel_names(chunk))
        return chunks

    @staticmethod
    def split_ampersand_counsel_names(name: str) -> list[str]:
        if " & " not in name:
            return [name]
        left, right = name.split(" & ", 1)
        if " " in left.strip() and " " in right.strip():
            return [left.strip(), right.strip()]
        return [name]

    @staticmethod
    def strip_firm_parentheticals(body: str) -> str:
        def replace(match: re.Match[str]) -> str:
            inner = match.group(1)
            return " " if FIRM_PARENTHETICAL.search(inner) else match.group(0)

        return re.sub(r"\(([^)]*)\)", replace, body)

    @staticmethod
    def merge_counsel_post_nominals(chunks: list[str]) -> list[str]:
        names: list[str] = []
        for chunk in chunks:
            token = chunk.strip()
            if token.upper() in COUNSEL_POST_NOMINALS and names:
                names[-1] = f"{names[-1]} {token.upper()}"
                continue
            names.append(token)
        return names

    @staticmethod
    def is_counsel_person_name(name: str) -> bool:
        if len(name) < 3:
            return False
        lowered = name.lower()
        if lowered in SKIP_COUNSEL_NAMES or name.upper() in COUNSEL_POST_NOMINALS:
            return False
        if "absent" in lowered or "unrepresented" in lowered:
            return False
        looks_like_party = lowered.startswith("the ") and any(word in lowered for word in PARTY_WORDS)
        return not looks_like_party


class CaseRawPromoter:
    @staticmethod
    def run(max_cases: int | None) -> None:
        raw_maker = get_raw_source_session_maker()
        knowledge_maker = get_knowledge_base_session_maker()

        with raw_maker() as raw_session, knowledge_maker() as knowledge_session:
            raw_repository = RawCaseRepository(raw_session)

            case_promoter = CasePromoter(
                raw_repository=raw_repository,
                knowledge_repository=KnowledgeCaseRepository(knowledge_session),
                parser=CaseDocumentParser(raw_repository)
            )

            promoted = case_promoter.promote_pending(max_cases)
            knowledge_session.commit()
            raw_session.commit()

        logger.info("Promote finished: %s cases", promoted)
