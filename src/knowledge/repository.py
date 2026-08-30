from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.knowledge.models.cases import (
    Case,
    CaseCounsel,
    CaseJudge,
    CaseParty,
    Counsel,
    Court,
    Judge,
    Party,
)
from src.knowledge.models.legislation import (
    Act,
    ActVersion,
    LegislativeDefinition,
    SubsidiaryLegislation,
)
from src.knowledge.models.paragraphs import Paragraph
from src.knowledge.models.provisions import Provision
from src.knowledge.models.taxonomy import FunctionalRole  # noqa: F401
from src.legislation.schema import ExtractedDefinition, FlattenedProvision


class KnowledgeCaseRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def persist[T](self, row: T) -> T:
        self.session.add(row)
        self.session.flush()
        return row

    def add_unless_present(self, existing: object | None, row: object) -> None:
        if existing is None:
            self.session.add(row)

    def get_existing_citations(self, citations: list[str]) -> set[str]:
        if not citations:
            return set()
        stmt = select(Case.neutral_citation).where(Case.neutral_citation.in_(citations))
        found = self.session.scalars(stmt)
        return set(found)

    def get_or_create_court(self, code: str, name: str) -> Court:
        court = self.session.scalar(select(Court).where(Court.code == code))
        return court if court is not None else self.persist(Court(code=code, name=name))

    def get_or_create_judge(self, full_name: str, title: str | None) -> Judge:
        judge = self.session.scalar(
            select(Judge).where(Judge.full_name == full_name, Judge.title == title)
        )
        return judge if judge is not None else self.persist(Judge(full_name=full_name, title=title))

    def get_or_create_party(self, name: str) -> Party:
        party = self.session.scalar(select(Party).where(Party.name == name))
        return party if party is not None else self.persist(Party(name=name))

    def get_or_create_counsel(self, name: str) -> Counsel:
        counsel = self.session.scalar(select(Counsel).where(Counsel.name == name))
        return counsel if counsel is not None else self.persist(Counsel(name=name))

    def add_case(
        self,
        court_id: int,
        decision_date: date,
        uri: str,
        title: str,
        neutral_citation: str,
        case_number: str | None,
    ) -> Case:
        return self.persist(
            Case(
                court_id=court_id,
                date=decision_date,
                uri=uri,
                title=title,
                neutral_citation=neutral_citation,
                case_number=case_number,
            )
        )

    def add_paragraphs(
        self,
        case_id: int,
        case_uri: str,
        paragraphs: list[tuple[int, str]],
    ) -> None:
        for ordinal, content in paragraphs:
            self.session.add(
                Paragraph(
                    case_id=case_id,
                    uri=f"{case_uri}#{ordinal}",
                    ordinal=ordinal,
                    content=content,
                )
            )

    def add_case_judge(self, case_id: int, judge_id: int) -> None:
        existing = self.session.scalar(
            select(CaseJudge).where(CaseJudge.case_id == case_id, CaseJudge.judge_id == judge_id)
        )
        self.add_unless_present(existing, CaseJudge(case_id=case_id, judge_id=judge_id))

    def add_case_party(self, case_id: int, party_id: int, role: str | None) -> None:
        existing = self.session.scalar(
            select(CaseParty).where(CaseParty.case_id == case_id, CaseParty.party_id == party_id)
        )
        self.add_unless_present(existing, CaseParty(case_id=case_id, party_id=party_id, role=role))

    def add_case_counsel(self, case_id: int, counsel_id: int, represents: str | None) -> None:
        existing = self.session.scalar(
            select(CaseCounsel).where(
                CaseCounsel.case_id == case_id,
                CaseCounsel.counsel_id == counsel_id,
            )
        )
        self.add_unless_present(
            existing,
            CaseCounsel(case_id=case_id, counsel_id=counsel_id, represents=represents),
        )


class KnowledgeLegislationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def persist[T](self, row: T) -> T:
        self.session.add(row)
        self.session.flush()
        return row

    def get_existing_act_version_uris(self, uris: list[str]) -> set[str]:
        if not uris:
            return set()
        stmt = select(ActVersion.uri).where(ActVersion.uri.in_(uris))
        found = self.session.scalars(stmt)
        return set(found)

    def get_existing_subsidiary_uris(self, uris: list[str]) -> set[str]:
        if not uris:
            return set()
        stmt = select(SubsidiaryLegislation.uri).where(SubsidiaryLegislation.uri.in_(uris))
        found = self.session.scalars(stmt)
        return set(found)

    def get_or_create_act(self, uri: str, title: str) -> Act:
        act = self.session.scalar(select(Act).where(Act.uri == uri))
        return act if act is not None else self.persist(Act(uri=uri, title=title))

    def add_act_version(
        self,
        act_id: int,
        uri: str,
        valid_from: date,
        is_current: bool,
    ) -> ActVersion:
        if is_current:
            current = self.session.scalar(
                select(ActVersion).where(
                    ActVersion.act_id == act_id,
                    ActVersion.is_current.is_(True),
                )
            )
            if current is not None:
                current.is_current = False

        return self.persist(
            ActVersion(
                act_id=act_id,
                uri=uri,
                valid_from=valid_from,
                is_current=is_current,
            )
        )

    def add_subsidiary_legislation(
        self,
        act_id: int | None,
        uri: str,
        title: str,
        number: str,
        instrument_date: date,
    ) -> SubsidiaryLegislation:
        return self.persist(
            SubsidiaryLegislation(
                act_id=act_id,
                uri=uri,
                title=title,
                number=number,
                date=instrument_date,
            )
        )

    def add_provisions(
        self,
        document_uri: str,
        provisions: list[FlattenedProvision],
        act_version_id: int | None = None,
        subsidiary_legislation_id: int | None = None,
    ) -> None:
        row_ids: list[int] = []
        used_uris: set[str] = set()

        for provision in provisions:
            parent_id = (
                row_ids[provision.parent_index]
                if provision.parent_index is not None
                else None
            )
            anchor = provision.anchor or f"n{provision.ordinal}"
            uri = f"{document_uri}#{anchor}"
            if uri in used_uris:
                uri = f"{document_uri}#{anchor}-{provision.ordinal}"
            used_uris.add(uri)

            row = self.persist(
                Provision(
                    act_version_id=act_version_id,
                    subsidiary_legislation_id=subsidiary_legislation_id,
                    parent_id=parent_id,
                    functional_role_id=None,
                    uri=uri,
                    kind=provision.kind,
                    ordinal=provision.ordinal,
                    level=provision.level,
                    citation=provision.citation,
                    heading=provision.heading,
                    content=provision.content,
                    amendment_note=provision.amendment_note,
                    descendant_count=provision.descendant_count,
                    embedding=None,
                )
            )
            row_ids.append(row.id)

    def replace_definitions(self, act_id: int, definitions: list[ExtractedDefinition]) -> None:
        stmt = delete(LegislativeDefinition).where(LegislativeDefinition.act_id == act_id)
        self.session.execute(stmt)

        seen: set[str] = set()
        for item in definitions:
            if item.term in seen:
                continue
            seen.add(item.term)
            self.session.add(
                LegislativeDefinition(
                    act_id=act_id,
                    term=item.term,
                    definition=item.definition,
                )
            )
