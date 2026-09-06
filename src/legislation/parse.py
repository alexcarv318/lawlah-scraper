from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup, NavigableString, Tag

from src.database import get_raw_source_session_maker
from src.knowledge.models.provisions import ProvisionKind
from src.legislation.schema import (
    ExtractedDefinition,
    ExtractedProvision,
    FlattenedProvision,
    LegislationParse,
    LegislationScrapeLimits,
)
from src.logger import get_logger
from src.notify.schema import FailureItem, StageReport, StageReporter, emit_report, stored_failures
from src.raw.models.cases import ParseStatus
from src.raw.repository import RawLegislationRepository

logger = get_logger(__name__)


class LegislationDocumentParser:
    def __init__(self, repository: RawLegislationRepository) -> None:
        self.repository = repository

    def parse_pending(
        self,
        max_versions: int | None,
        reporter: StageReporter | None = None,
        limits: LegislationScrapeLimits | None = None,
    ) -> StageReport:
        persist_every = (limits or LegislationScrapeLimits()).parse_persist_every
        pending = self.repository.get_act_versions_pending_parse(max_versions)
        logger.info("Parsing %s pending act versions", len(pending))

        counts: Counter[ParseStatus] = Counter()
        failures: list[FailureItem] = []
        for parsed, (act, version) in enumerate(pending, start=1):
            result = self.parse_html(version.html)
            self.repository.save_act_version_parse(act, version, result)
            counts[result.parse_status] += 1

            label = f"{act.slug} {version.valid_from.isoformat()}"
            self.log_parse(act.slug, version.valid_from.isoformat(), result)
            failure = self.parse_failure(label, result)
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
            "Parsed %s act versions: %s complete, %s incomplete, %s unknown_layout, %s failed",
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

    def parse_pending_subsidiary(
        self,
        max_versions: int | None,
        limits: LegislationScrapeLimits | None = None,
    ) -> int:
        persist_every = (limits or LegislationScrapeLimits()).parse_persist_every
        pending = self.repository.get_subsidiary_versions_pending_parse(max_versions)
        logger.info("Parsing %s pending subsidiary legislation versions", len(pending))

        counts: Counter[ParseStatus] = Counter()
        for parsed, (instrument, version) in enumerate(pending, start=1):
            result = self.parse_html(version.html)
            self.repository.save_subsidiary_version_parse(instrument, version, result)
            counts[result.parse_status] += 1
            self.log_parse(instrument.slug, version.valid_from.isoformat(), result)
            if parsed % persist_every == 0:
                self.repository.session.commit()

        logger.info(
            "Parsed %s subsidiary legislation versions: %s complete, %s incomplete, %s unknown_layout, %s failed",
            len(pending),
            counts[ParseStatus.COMPLETE],
            counts[ParseStatus.INCOMPLETE],
            counts[ParseStatus.UNKNOWN_LAYOUT],
            counts[ParseStatus.FAILED],
        )
        return len(pending)

    @staticmethod
    def log_parse(slug: str, valid_from: str, result: LegislationParse) -> None:
        if result.parse_status == ParseStatus.INCOMPLETE:
            logger.warning(
                "Incomplete parse for %s %s: expected=%s extracted=%s",
                slug,
                valid_from,
                result.expected_provision_count,
                result.extracted_provision_count,
            )
        elif result.parse_status == ParseStatus.UNKNOWN_LAYOUT:
            logger.warning("Unknown layout for %s %s", slug, valid_from)
        elif result.parse_status == ParseStatus.FAILED:
            logger.warning("Parse failed for %s %s", slug, valid_from)

    @staticmethod
    def parse_failure(label: str, result: LegislationParse) -> FailureItem | None:
        if result.parse_status == ParseStatus.INCOMPLETE:
            return FailureItem(
                label,
                (
                    f"incomplete: expected={result.expected_provision_count} "
                    f"extracted={result.extracted_provision_count}"
                ),
            )
        if result.parse_status == ParseStatus.UNKNOWN_LAYOUT:
            return FailureItem(label, "unknown layout")
        if result.parse_status == ParseStatus.FAILED:
            return FailureItem(label, "parse failed")
        return None

    @staticmethod
    def parse_html(html: str | None) -> LegislationParse:
        if html is None or not html.strip():
            return LegislationParse.failed()

        soup = BeautifulSoup(html, "lxml")
        root = soup.select_one("div.legis")
        if root is None:
            root = soup

        expected = (
            len(root.select("div.prov1"))
            + len(root.select("div.prov1Rep"))
            + len(root.select("div.schedule"))
        )
        if expected == 0:
            return LegislationParse.unknown_layout()

        provisions = LegislationDocumentParser.provisions_from_root(root)
        definitions = LegislationDocumentParser.definitions_from_root(root)
        extracted = LegislationDocumentParser.toc_item_count(provisions)
        return LegislationParse.from_tree(expected, extracted, provisions, definitions)

    @staticmethod
    def flatten(provisions: list[ExtractedProvision]) -> list[FlattenedProvision]:
        flattened: list[FlattenedProvision] = []

        def walk(
            nodes: list[ExtractedProvision],
            parent_index: int | None,
            level: int,
        ) -> None:
            for node in nodes:
                index = len(flattened)
                flattened.append(
                    FlattenedProvision(
                        kind=node.kind,
                        citation=node.citation,
                        heading=node.heading,
                        content=node.content,
                        amendment_note=node.amendment_note,
                        anchor=node.anchor,
                        parent_index=parent_index,
                        ordinal=index + 1,
                        level=level,
                        descendant_count=0,
                    )
                )
                walk(node.children, index, level + 1)
                flattened[index] = FlattenedProvision(
                    kind=node.kind,
                    citation=node.citation,
                    heading=node.heading,
                    content=node.content,
                    amendment_note=node.amendment_note,
                    anchor=node.anchor,
                    parent_index=parent_index,
                    ordinal=index + 1,
                    level=level,
                    descendant_count=len(flattened) - index - 1,
                )

        walk(provisions, None, 1)
        return flattened

    @staticmethod
    def provisions_from_root(root: Tag) -> list[ExtractedProvision]:
        provisions: list[ExtractedProvision] = []

        front = root.select_one("div.front")
        if front is not None:
            opening = LegislationDocumentParser.opening_from_front(front)
            if opening is not None:
                provisions.append(opening)

        body = root.select_one("div.body")
        if body is not None:
            provisions.extend(LegislationDocumentParser.provisions_from_body(body))

        tail = root.select_one("div.tail")
        if tail is not None:
            for schedule in tail.select("div.schedule"):
                provisions.append(LegislationDocumentParser.schedule_from_tag(schedule))

        return provisions

    @staticmethod
    def provisions_from_body(body: Tag) -> list[ExtractedProvision]:
        provisions: list[ExtractedProvision] = []

        for child in body.children:
            if not isinstance(child, Tag):
                continue

            part = child.select_one("td.part")
            if part is not None:
                provisions.append(LegislationDocumentParser.part_from_tag(part))
                continue

            division = child.select_one("td.div")
            if division is not None:
                provisions.append(LegislationDocumentParser.division_from_tag(division))
                continue

            names = LegislationDocumentParser.class_names(child)
            if "prov1" in names or "prov1Rep" in names:
                provisions.append(LegislationDocumentParser.section_from_tag(child))

        return provisions

    @staticmethod
    def opening_from_front(front: Tag) -> ExtractedProvision | None:
        for table in front.find_all("table"):
            text = LegislationDocumentParser.visible_text(table)
            if text.startswith("An Act "):
                notes = LegislationDocumentParser.amendment_notes(table)
                return ExtractedProvision(
                    kind=ProvisionKind.OPENING,
                    citation=None,
                    heading=None,
                    content=text,
                    amendment_note=notes,
                    anchor=LegislationDocumentParser.attribute(table, "id") or "opening",
                    children=[],
                )
        return None

    @staticmethod
    def part_from_tag(part: Tag) -> ExtractedProvision:
        number_text = None
        heading = None
        children: list[ExtractedProvision] = []

        for child in part.children:
            if not isinstance(child, Tag):
                continue

            names = LegislationDocumentParser.class_names(child)
            if "partNo" in names:
                number_text = LegislationDocumentParser.visible_text(child)
                continue
            if "prov1" in names or "prov1Rep" in names:
                children.append(LegislationDocumentParser.section_from_tag(child))
                continue

            inner_division = child.select_one("td.div") if child.name == "table" else None
            if inner_division is not None:
                children.append(LegislationDocumentParser.division_from_tag(inner_division))
                continue

            if child.name == "table" and child.select_one("div.prov1") is None:
                heading_text = LegislationDocumentParser.visible_text(child)
                if heading_text:
                    heading = heading_text

        citation = LegislationDocumentParser.part_citation(number_text)
        return ExtractedProvision(
            kind=ProvisionKind.PART,
            citation=citation,
            heading=heading,
            content=None,
            amendment_note=None,
            anchor=LegislationDocumentParser.attribute(part, "id"),
            children=children,
        )

    @staticmethod
    def division_from_tag(division: Tag) -> ExtractedProvision:
        number_text = None
        heading = None
        children: list[ExtractedProvision] = []

        for child in division.children:
            if not isinstance(child, Tag):
                continue

            names = LegislationDocumentParser.class_names(child)
            if "divNo" in names:
                number_text = LegislationDocumentParser.visible_text(child)
                continue
            if "prov1" in names or "prov1Rep" in names:
                children.append(LegislationDocumentParser.section_from_tag(child))
                continue

            if child.name == "table" and child.select_one("div.prov1") is None:
                heading_text = LegislationDocumentParser.visible_text(child)
                if heading_text:
                    heading = heading_text

        citation = LegislationDocumentParser.division_citation(number_text)
        return ExtractedProvision(
            kind=ProvisionKind.DIVISION,
            citation=citation,
            heading=heading,
            content=None,
            amendment_note=None,
            anchor=LegislationDocumentParser.attribute(division, "id"),
            children=children,
        )

    @staticmethod
    def section_from_tag(section: Tag) -> ExtractedProvision:
        header = LegislationDocumentParser.first_cell(section, "prov1Hdr")
        body = LegislationDocumentParser.first_cell(section, "prov1Txt")
        if body is None:
            body = LegislationDocumentParser.first_cell(section, "prov1RepText")

        heading = LegislationDocumentParser.visible_text(header) if header is not None else None
        anchor = None
        if header is not None:
            anchor = LegislationDocumentParser.attribute(header, "id")
        if not anchor:
            anchor = LegislationDocumentParser.attribute(section, "id")

        number = LegislationDocumentParser.section_number(body)
        citation = f"s {number}" if number else None
        content, children, notes = LegislationDocumentParser.blocks_from_container(
            body,
            citation,
        )
        content = LegislationDocumentParser.strip_leading_number(content)

        return ExtractedProvision(
            kind=ProvisionKind.SECTION,
            citation=citation,
            heading=heading or None,
            content=content,
            amendment_note=notes,
            anchor=anchor,
            children=children,
        )

    @staticmethod
    def schedule_from_tag(schedule: Tag) -> ExtractedProvision:
        header = LegislationDocumentParser.first_cell(schedule, "sHdr")
        heading = LegislationDocumentParser.visible_text(header) if header is not None else None
        citation = LegislationDocumentParser.schedule_citation(heading)
        anchor = LegislationDocumentParser.attribute(header, "id") if header is not None else None
        subject = LegislationDocumentParser.first_cell(schedule, "scHdr")
        subject_text = (
            LegislationDocumentParser.visible_text(subject) if subject is not None else None
        )
        children: list[ExtractedProvision] = []
        pending_heading: str | None = None

        for table in schedule.find_all("table", recursive=False):
            if not isinstance(table, Tag):
                continue
            if LegislationDocumentParser.first_cell(table, "sHdr") is not None:
                continue
            if LegislationDocumentParser.first_cell(table, "SbodyRefs") is not None:
                continue
            if LegislationDocumentParser.first_cell(table, "scHdr") is not None:
                continue

            paragraph_heading = LegislationDocumentParser.first_cell(table, "sProvHdr")
            if paragraph_heading is not None:
                pending_heading = LegislationDocumentParser.visible_text(paragraph_heading) or None
                continue

            paragraph_body = LegislationDocumentParser.first_cell(table, "tailSTxt")
            if paragraph_body is None:
                continue

            children.append(
                LegislationDocumentParser.schedule_paragraph(
                    paragraph_body,
                    citation,
                    pending_heading,
                )
            )
            pending_heading = None

        notes = LegislationDocumentParser.amendment_notes(header) if header is not None else None
        return ExtractedProvision(
            kind=ProvisionKind.SCHEDULE,
            citation=citation,
            heading=heading or subject_text,
            content=subject_text if subject_text != heading else None,
            amendment_note=notes,
            anchor=anchor,
            children=children,
        )

    @staticmethod
    def schedule_paragraph(
        body: Tag,
        schedule_citation: str | None,
        heading: str | None,
    ) -> ExtractedProvision:
        text = LegislationDocumentParser.visible_text(body)
        number_match = re.match(r"^(\d+[A-Z]?)\.", text)
        number = number_match.group(1) if number_match is not None else None
        citation = (
            f"{schedule_citation} para {number}"
            if schedule_citation is not None and number is not None
            else schedule_citation
        )
        content, children, notes = LegislationDocumentParser.blocks_from_container(body, citation)
        content = LegislationDocumentParser.strip_leading_number(content)
        return ExtractedProvision(
            kind=ProvisionKind.SECTION,
            citation=citation,
            heading=heading,
            content=content,
            amendment_note=notes,
            anchor=LegislationDocumentParser.attribute(body, "id"),
            children=children,
        )

    @staticmethod
    def blocks_from_container(
        container: Tag | None,
        parent_citation: str | None,
    ) -> tuple[str | None, list[ExtractedProvision], str | None]:
        if container is None:
            return None, [], None

        subsections = LegislationDocumentParser.subsection_elements(container)
        if subsections:
            lead = LegislationDocumentParser.clone_without(
                container,
                subsections,
            )
            content = LegislationDocumentParser.visible_text(lead) or None
            children = [
                LegislationDocumentParser.subsection_from_tag(item, parent_citation)
                for item in subsections
            ]
            notes = LegislationDocumentParser.amendment_notes(lead)
            return content, children, notes

        return LegislationDocumentParser.points_and_text(container, parent_citation)

    @staticmethod
    def subsection_from_tag(tag: Tag, section_citation: str | None) -> ExtractedProvision:
        isolated = LegislationDocumentParser.clone_without(
            tag,
            LegislationDocumentParser.descendant_subsections(tag),
        )
        text = LegislationDocumentParser.visible_text(isolated)
        number = LegislationDocumentParser.subsection_number(text)
        citation = (
            f"{section_citation}({number})"
            if section_citation is not None and number is not None
            else section_citation
        )
        content, children, notes = LegislationDocumentParser.points_and_text(isolated, citation)
        content = LegislationDocumentParser.strip_subsection_marker(content)
        return ExtractedProvision(
            kind=ProvisionKind.SUBSECTION,
            citation=citation,
            heading=None,
            content=content,
            amendment_note=notes,
            anchor=LegislationDocumentParser.subsection_anchor(tag),
            children=children,
        )

    @staticmethod
    def points_and_text(
        container: Tag,
        parent_citation: str | None,
    ) -> tuple[str | None, list[ExtractedProvision], str | None]:
        tables = LegislationDocumentParser.top_point_tables(container)
        children: list[ExtractedProvision] = []
        for table in tables:
            children.extend(LegislationDocumentParser.points_from_table(table, parent_citation))

        remainder = LegislationDocumentParser.clone_without(container, tables)
        content = LegislationDocumentParser.visible_text(remainder) or None
        notes = LegislationDocumentParser.amendment_notes(remainder)
        return content, children, notes

    @staticmethod
    def points_from_table(
        table: Tag,
        parent_citation: str | None,
    ) -> list[ExtractedProvision]:
        points: list[ExtractedProvision] = []
        body = table.find("tbody")
        rows = body if isinstance(body, Tag) else table

        for row in rows.children:
            if not isinstance(row, Tag) or row.name != "tr":
                continue

            marker_cell = None
            text_cell = None
            for cell in row.children:
                if not isinstance(cell, Tag) or cell.name != "td":
                    continue
                names = LegislationDocumentParser.class_names(cell)
                if names & {"p1No", "p2No", "p3No", "p1DefNo", "sProvP1No"}:
                    marker_cell = cell
                elif names & {"pTxt", "p2Txt", "p3Txt", "pDefTxt", "sProvP1"}:
                    text_cell = cell

            if marker_cell is None or text_cell is None:
                continue

            marker = re.sub(r"\s+", "", LegislationDocumentParser.visible_text(marker_cell))
            citation = (
                f"{parent_citation}{marker}"
                if parent_citation is not None and marker
                else marker or None
            )
            content, children, notes = LegislationDocumentParser.points_and_text(
                text_cell,
                citation,
            )
            points.append(
                ExtractedProvision(
                    kind=ProvisionKind.POINT,
                    citation=citation,
                    heading=None,
                    content=content,
                    amendment_note=notes,
                    anchor=LegislationDocumentParser.attribute(text_cell, "id"),
                    children=children,
                )
            )

        return points

    @staticmethod
    def definitions_from_root(root: Tag) -> list[ExtractedDefinition]:
        definitions: list[ExtractedDefinition] = []
        quote = re.compile(r"[“\"]([^”\"]+)[”\"]")
        connective = re.compile(
            r"\b(means|mean|includes|include|has the meaning|have the meaning|"
            r"shall mean|shall include|does not include|do not include)\b",
            re.IGNORECASE,
        )

        for cell in root.select("td.def"):
            text = LegislationDocumentParser.visible_text(cell)
            quoted = list(quote.finditer(text))
            if not quoted:
                continue

            stop = connective.search(text)
            terms: list[str] = []
            for match in quoted:
                if stop is not None and match.start() > stop.start():
                    break
                term = match.group(1).strip()
                if term:
                    terms.append(term)

            definition = text[quoted[0].end() :].strip()
            if not terms or not definition:
                continue

            for term in terms:
                definitions.append(ExtractedDefinition(term=term, definition=definition))

        return definitions

    @staticmethod
    def toc_item_count(
        provisions: list[ExtractedProvision],
        inside_schedule: bool = False,
    ) -> int:
        total = 0
        for node in provisions:
            if node.kind == ProvisionKind.SCHEDULE:
                total += 1
                total += LegislationDocumentParser.toc_item_count(node.children, True)
            elif node.kind == ProvisionKind.SECTION and not inside_schedule:
                total += 1
                total += LegislationDocumentParser.toc_item_count(node.children, False)
            else:
                total += LegislationDocumentParser.toc_item_count(node.children, inside_schedule)
        return total

    @staticmethod
    def subsection_elements(container: Tag) -> list[Tag]:
        found: list[Tag] = []
        for element in container.find_all(True):
            if not isinstance(element, Tag) or element is container:
                continue
            names = LegislationDocumentParser.class_names(element)
            if "prov2TxtIL" in names or "prov2Txt" in names:
                found.append(element)
        return found

    @staticmethod
    def descendant_subsections(container: Tag) -> list[Tag]:
        return [
            element
            for element in LegislationDocumentParser.subsection_elements(container)
            if element is not container
        ]

    @staticmethod
    def top_point_tables(container: Tag) -> list[Tag]:
        tables: list[Tag] = []
        for table in container.find_all("table"):
            if not isinstance(table, Tag) or table is container:
                continue
            if not LegislationDocumentParser.is_point_table(table):
                continue
            if LegislationDocumentParser.inside_definition(table, container):
                continue
            tables.append(table)

        top: list[Tag] = []
        for table in tables:
            nested = any(table is not other and table in other.descendants for other in tables)
            if not nested:
                top.append(table)
        return top

    @staticmethod
    def is_point_table(table: Tag) -> bool:
        for cell in table.find_all("td"):
            if not isinstance(cell, Tag):
                continue
            if LegislationDocumentParser.class_names(cell) & {
                "p1No",
                "p2No",
                "p3No",
                "p1DefNo",
                "sProvP1No",
            }:
                return True
        return False

    @staticmethod
    def inside_definition(tag: Tag, limit: Tag) -> bool:
        for parent in tag.parents:
            if parent is limit:
                return False
            if isinstance(parent, Tag) and "def" in LegislationDocumentParser.class_names(parent):
                return True
        return False

    @staticmethod
    def clone_without(tag: Tag, drop: list[Tag]) -> Tag:
        for item in drop:
            item["data-lawlah-drop"] = "1"
        clone = LegislationDocumentParser.clone_tag(tag)
        for item in drop:
            del item["data-lawlah-drop"]
        for element in clone.select("[data-lawlah-drop]"):
            element.decompose()
        return clone

    @staticmethod
    def clone_tag(tag: Tag) -> Tag:
        soup = BeautifulSoup(str(tag), "lxml")
        found = soup.find(tag.name)
        if not isinstance(found, Tag):
            raise TypeError("Failed to clone legislation HTML tag")
        return found

    @staticmethod
    def first_cell(root: Tag, class_name: str) -> Tag | None:
        for cell in root.find_all("td"):
            if not isinstance(cell, Tag):
                continue
            if class_name in LegislationDocumentParser.class_names(cell):
                return cell
        return None

    @staticmethod
    def section_number(body: Tag | None) -> str | None:
        if body is None:
            return None
        for child in body.children:
            if isinstance(child, Tag) and child.name == "strong":
                match = re.match(
                    r"^(\d+[A-Z]*)",
                    LegislationDocumentParser.visible_text(child),
                )
                return match.group(1) if match is not None else None
        return None

    @staticmethod
    def subsection_number(text: str) -> str | None:
        match = re.match(r"^—?\s*\((\d+[A-Z]?)\)", text)
        return match.group(1) if match is not None else None

    @staticmethod
    def strip_leading_number(text: str | None) -> str | None:
        if text is None:
            return None
        stripped = re.sub(r"^\d+[A-Z]*\.\s*", "", text).strip()
        return stripped or None

    @staticmethod
    def strip_subsection_marker(text: str | None) -> str | None:
        if text is None:
            return None
        stripped = re.sub(r"^—?\s*\(\d+[A-Z]?\)\s*", "", text).strip()
        return stripped or None

    @staticmethod
    def part_citation(text: str | None) -> str | None:
        if text is None:
            return None
        match = re.match(r"^PART\s+(\S+)", text, re.IGNORECASE)
        return f"Pt {match.group(1)}" if match is not None else None

    @staticmethod
    def division_citation(text: str | None) -> str | None:
        if text is None:
            return None
        match = re.match(r"^DIVISION\s+(\S+)", text, re.IGNORECASE)
        return f"Div {match.group(1)}" if match is not None else None

    @staticmethod
    def schedule_citation(text: str | None) -> str | None:
        if text is None:
            return None

        words = {
            "first": "1",
            "second": "2",
            "third": "3",
            "fourth": "4",
            "fifth": "5",
            "sixth": "6",
            "seventh": "7",
            "eighth": "8",
            "ninth": "9",
            "tenth": "10",
            "eleventh": "11",
            "twelfth": "12",
        }
        match = re.match(r"^(FIRST|SECOND|THIRD|FOURTH|FIFTH|SIXTH|SEVENTH|"
                         r"EIGHTH|NINTH|TENTH|ELEVENTH|TWELFTH)\s+SCHEDULE\b",
                         text,
                         re.IGNORECASE)
        if match is not None:
            return f"Sch {words[match.group(1).lower()]}"

        numbered = re.match(r"^(\d+)(?:ST|ND|RD|TH)?\s+SCHEDULE\b", text, re.IGNORECASE)
        if numbered is not None:
            return f"Sch {numbered.group(1)}"

        if re.match(r"^SCHEDULE\b", text, re.IGNORECASE):
            return "Sch"
        return None

    @staticmethod
    def subsection_anchor(tag: Tag) -> str | None:
        named = tag.find("a", attrs={"name": True})
        if isinstance(named, Tag):
            name = named.get("name")
            if isinstance(name, str) and name:
                return name
        return LegislationDocumentParser.attribute(tag, "id")

    @staticmethod
    def visible_text(tag: Tag) -> str:
        parts: list[str] = []

        def walk(node: Tag) -> None:
            for child in node.children:
                if isinstance(child, Tag):
                    if "amendNote" in LegislationDocumentParser.class_names(child):
                        continue
                    walk(child)
                elif isinstance(child, NavigableString):
                    text = str(child)
                    if text.strip():
                        parts.append(text)

        walk(tag)
        return " ".join(" ".join(parts).split())

    @staticmethod
    def amendment_notes(tag: Tag | None) -> str | None:
        if tag is None:
            return None
        notes: list[str] = []
        for note in tag.select("div.amendNote"):
            if any(
                "amendNote" in LegislationDocumentParser.class_names(parent)
                for parent in note.parents
                if isinstance(parent, Tag) and parent is not tag
            ):
                continue
            text = " ".join(note.get_text(" ", strip=True).split())
            if text:
                notes.append(text)
        return "\n".join(notes) if notes else None

    @staticmethod
    def class_names(tag: Tag) -> set[str]:
        value = tag.get("class")
        if isinstance(value, list):
            return {str(item) for item in value}
        if isinstance(value, str) and value:
            return {value}
        return set()

    @staticmethod
    def attribute(tag: Tag, name: str) -> str | None:
        value = tag.get(name)
        return value if isinstance(value, str) and value else None


class LegislationRawParser:
    @staticmethod
    def run(
        max_versions: int | None = None,
        reporter: StageReporter | None = None,
    ) -> StageReport:
        session_maker = get_raw_source_session_maker()
        with session_maker() as session:
            parser = LegislationDocumentParser(RawLegislationRepository(session))
            limits = LegislationScrapeLimits()
            parsed_acts = parser.parse_pending(max_versions, reporter, limits)
            parsed_subsidiary = parser.parse_pending_subsidiary(max_versions, limits)
            session.commit()

        logger.info(
            "Raw legislation parse finished: %s act versions, %s subsidiary legislation versions",
            parsed_acts.counts.get("parsed", 0),
            parsed_subsidiary,
        )
        return parsed_acts
