from datetime import date

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from src.aliases.schema import ExtractedAlias
from src.knowledge.models.aliases import Alias
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
from src.knowledge.models.references import Reference
from src.knowledge.models.taxonomy import (
    Concept,
    FunctionalRole,
    FunctionalRoleAppliesTo,
    ParagraphConcept,
    ParagraphTopic,
    ProvisionConcept,
    ProvisionTopic,
    Topic,
)
from src.legislation.schema import ExtractedDefinition, FlattenedProvision
from src.references.schema import ExtractedReference


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

    def get_paragraphs_pending_embedding(self, limit: int | None) -> list[Paragraph]:
        stmt = (
            select(Paragraph)
            .where(Paragraph.embedding.is_(None))
            .order_by(Paragraph.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        found = self.session.scalars(stmt)
        return list(found)

    @staticmethod
    def save_paragraph_embedding(paragraph: Paragraph, embedding: list[float]) -> None:
        paragraph.embedding = embedding

    def get_paragraphs_pending_classification(self, limit: int | None) -> list[Paragraph]:
        stmt = (
            select(Paragraph)
            .where(Paragraph.functional_role_id.is_(None))
            .order_by(Paragraph.case_id, Paragraph.ordinal)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        found = self.session.scalars(stmt)
        return list(found)

    def save_paragraph_classification(
        self,
        paragraph: Paragraph,
        role_id: int,
        topic_ids: list[int],
        concept_ids: list[int],
    ) -> None:
        paragraph.functional_role_id = role_id
        for topic_id in topic_ids:
            self.session.add(ParagraphTopic(paragraph_id=paragraph.id, topic_id=topic_id))
        for concept_id in concept_ids:
            self.session.add(ParagraphConcept(paragraph_id=paragraph.id, concept_id=concept_id))

    def get_cases_with_paragraphs(
        self,
        limit: int | None,
    ) -> list[tuple[Case, list[Paragraph]]]:
        stmt = select(Case).order_by(Case.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.attach_paragraphs(list(self.session.scalars(stmt)))

    def get_cases_pending_citation_extraction(
        self,
        limit: int | None,
    ) -> list[tuple[Case, list[Paragraph]]]:
        has_paragraph = select(Paragraph.id).where(Paragraph.case_id == Case.id).exists()
        has_reference = (
            select(Reference.id)
            .join(Paragraph, Reference.source_paragraph_id == Paragraph.id)
            .where(Paragraph.case_id == Case.id)
            .exists()
        )
        stmt = select(Case).where(has_paragraph, ~has_reference).order_by(Case.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return self.attach_paragraphs(list(self.session.scalars(stmt)))

    def attach_paragraphs(self, cases: list[Case]) -> list[tuple[Case, list[Paragraph]]]:
        if not cases:
            return []

        paragraph_stmt = (
            select(Paragraph)
            .where(Paragraph.case_id.in_([case.id for case in cases]))
            .order_by(Paragraph.case_id, Paragraph.ordinal)
        )
        by_case: dict[int, list[Paragraph]] = {case.id: [] for case in cases}
        for paragraph in self.session.scalars(paragraph_stmt):
            by_case[paragraph.case_id].append(paragraph)
        return [(case, by_case[case.id]) for case in cases]

    def get_aliases_for_case(self, case_id: int) -> list[Alias]:
        stmt = select(Alias).where(Alias.case_id == case_id).order_by(Alias.id)
        found = self.session.scalars(stmt)
        return list(found)

    def replace_aliases(self, case_id: int, aliases: list[ExtractedAlias]) -> dict[str, int]:
        stmt = delete(Alias).where(Alias.case_id == case_id)
        self.session.execute(stmt)

        ids: dict[str, int] = {}
        for item in aliases:
            row = self.persist(
                Alias(
                    case_id=case_id,
                    short_name=item.short_name,
                    expanded_text=item.expanded_text,
                    target_act_id=None,
                    target_case_id=None,
                )
            )
            ids[item.short_name] = row.id
        return ids

    def replace_references(
        self,
        case_id: int,
        references: list[ExtractedReference],
        alias_ids: dict[str, int],
    ) -> list[Reference]:
        paragraph_ids = select(Paragraph.id).where(Paragraph.case_id == case_id)
        stmt = delete(Reference).where(Reference.source_paragraph_id.in_(paragraph_ids))
        self.session.execute(stmt)

        rows: list[Reference] = []
        for item in references:
            alias_id = (
                alias_ids.get(item.alias_short_name)
                if item.alias_short_name is not None
                else None
            )
            row = Reference(
                source_paragraph_id=item.source_paragraph_id,
                alias_id=alias_id,
                quoted_text=item.quoted_text,
                kind=item.kind,
                alias_short_name=item.alias_short_name,
                title=item.title,
                neutral_citation=item.neutral_citation,
                slr_citation=item.slr_citation,
                edition=item.edition,
                provision_citations=list(item.provision_citations),
                paragraph_pins=list(item.paragraph_pins),
            )
            self.session.add(row)
            rows.append(row)
        self.session.flush()
        return rows

    def get_unresolved_references(self, limit: int | None) -> list[Reference]:
        stmt = (
            select(Reference)
            .where(Reference.target_act_id.is_(None))
            .where(Reference.target_provision_id.is_(None))
            .where(Reference.target_case_id.is_(None))
            .where(Reference.target_paragraph_id.is_(None))
            .order_by(Reference.id)
        )
        if limit is not None:
            stmt = stmt.limit(limit)
        found = self.session.scalars(stmt)
        return list(found)

    def get_cases_by_neutral_citations(self, citations: list[str]) -> dict[str, Case]:
        if not citations:
            return {}
        stmt = select(Case).where(Case.neutral_citation.in_(citations))
        found: dict[str, Case] = {}
        for case in self.session.scalars(stmt):
            found[case.neutral_citation] = case
        return found

    def get_paragraphs_by_ids(self, paragraph_ids: list[int]) -> dict[int, Paragraph]:
        if not paragraph_ids:
            return {}
        stmt = select(Paragraph).where(Paragraph.id.in_(paragraph_ids))
        found: dict[int, Paragraph] = {}
        for paragraph in self.session.scalars(stmt):
            found[paragraph.id] = paragraph
        return found

    def get_aliases_by_ids(self, alias_ids: list[int]) -> dict[int, Alias]:
        if not alias_ids:
            return {}
        stmt = select(Alias).where(Alias.id.in_(alias_ids))
        found: dict[int, Alias] = {}
        for alias in self.session.scalars(stmt):
            found[alias.id] = alias
        return found

    def get_paragraphs_by_case_ordinal(
        self,
        case_ids: list[int],
    ) -> dict[tuple[int, int], Paragraph]:
        if not case_ids:
            return {}
        stmt = select(Paragraph).where(Paragraph.case_id.in_(case_ids))
        found: dict[tuple[int, int], Paragraph] = {}
        for paragraph in self.session.scalars(stmt):
            found[(paragraph.case_id, paragraph.ordinal)] = paragraph
        return found


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
                    embedding_text=None,
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

    def get_current_provision_trees(self) -> list[list[Provision]]:
        act_stmt = (
            select(Provision)
            .join(ActVersion, Provision.act_version_id == ActVersion.id)
            .where(ActVersion.is_current.is_(True))
            .order_by(Provision.act_version_id, Provision.ordinal)
        )
        subsidiary_stmt = (
            select(Provision)
            .where(Provision.subsidiary_legislation_id.is_not(None))
            .order_by(Provision.subsidiary_legislation_id, Provision.ordinal)
        )

        by_act_version: dict[int, list[Provision]] = {}
        for provision in self.session.scalars(act_stmt):
            if provision.act_version_id is None:
                continue
            by_act_version.setdefault(provision.act_version_id, []).append(provision)

        by_subsidiary: dict[int, list[Provision]] = {}
        for provision in self.session.scalars(subsidiary_stmt):
            if provision.subsidiary_legislation_id is None:
                continue
            by_subsidiary.setdefault(provision.subsidiary_legislation_id, []).append(provision)

        return [*by_act_version.values(), *by_subsidiary.values()]

    def save_provision_classification(
        self,
        provision: Provision,
        role_id: int,
        topic_ids: list[int],
        concept_ids: list[int],
    ) -> None:
        provision.functional_role_id = role_id
        for topic_id in topic_ids:
            self.session.add(ProvisionTopic(provision_id=provision.id, topic_id=topic_id))
        for concept_id in concept_ids:
            self.session.add(ProvisionConcept(provision_id=provision.id, concept_id=concept_id))

    @staticmethod
    def save_provision_embedding(
        provision: Provision,
        embedding: list[float],
        embedding_text: str,
    ) -> None:
        provision.embedding = embedding
        provision.embedding_text = embedding_text


class KnowledgeTaxonomyRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_roles(self) -> dict[str, FunctionalRole]:
        found = list(self.session.scalars(select(FunctionalRole)))
        if not found:
            raise ValueError("No functional_roles in knowledge_base; seed taxonomy first")
        return {row.name: row for row in found}

    def get_topics(self) -> dict[str, Topic]:
        found = list(self.session.scalars(select(Topic)))
        if not found:
            raise ValueError("No topics in knowledge_base; seed taxonomy first")
        return {row.name: row for row in found}

    def get_concepts(self) -> dict[str, Concept]:
        found = list(self.session.scalars(select(Concept)))
        if not found:
            raise ValueError("No concepts in knowledge_base; seed taxonomy first")
        return {row.name: row for row in found}

    def persist[T](self, row: T) -> T:
        self.session.add(row)
        self.session.flush()
        return row

    def ensure_topic(self, name: str) -> tuple[Topic, bool]:
        existing = self.session.scalar(select(Topic).where(Topic.name == name))
        if existing is not None:
            return existing, False
        return self.persist(Topic(name=name, description=name)), True

    def ensure_concept(self, name: str, topic_id: int) -> tuple[Concept, bool]:
        existing = self.session.scalar(select(Concept).where(Concept.name == name))
        if existing is not None:
            if existing.topic_id != topic_id:
                existing.topic_id = topic_id
            return existing, False
        return self.persist(Concept(name=name, topic_id=topic_id, description=name)), True

    def ensure_role(
        self,
        name: str,
        applies_to: FunctionalRoleAppliesTo,
    ) -> tuple[FunctionalRole, bool]:
        existing = self.session.scalar(select(FunctionalRole).where(FunctionalRole.name == name))
        if existing is not None:
            if existing.applies_to != applies_to:
                raise ValueError(
                    f"Role {name!r} already applies to {existing.applies_to.value}, "
                    f"cannot seed as {applies_to.value}"
                )
            return existing, False
        return (
            self.persist(
                FunctionalRole(name=name, description=name, applies_to=applies_to)
            ),
            True,
        )
