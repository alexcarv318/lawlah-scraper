from src.database import get_knowledge_base_session_maker, get_raw_source_session_maker
from src.knowledge.repository import KnowledgeLegislationRepository
from src.legislation.parse import LegislationDocumentParser
from src.logger import get_logger
from src.raw.models.cases import ParseStatus
from src.raw.models.legislation import RawAct, RawActVersion
from src.raw.repository import RawLegislationRepository

logger = get_logger(__name__)


class LegislationPromoter:
    def __init__(
        self,
        raw_repository: RawLegislationRepository,
        knowledge_repository: KnowledgeLegislationRepository,
        parser: LegislationDocumentParser,
    ) -> None:
        self.raw_repository = raw_repository
        self.knowledge_repository = knowledge_repository
        self.parser = parser

    def promote_pending(self, max_versions: int | None) -> int:
        pending = self.raw_repository.get_act_versions_pending_promote(max_versions)
        version_uris = [
            LegislationPromoter.act_version_uri(act, version)
            for act, version in pending
        ]
        already_promoted = self.knowledge_repository.get_existing_act_version_uris(version_uris)

        logger.info(
            "Promoting %s complete act versions (%s already in knowledge base)",
            len(pending),
            len(already_promoted),
        )

        promoted = 0
        skipped = 0
        for act, version in pending:
            version_uri = LegislationPromoter.act_version_uri(act, version)
            if version_uri in already_promoted:
                self.raw_repository.mark_act_version_promoted(version)
                skipped += 1
                continue
            if not self.promote_act_version(act, version, version_uri):
                skipped += 1
                continue
            self.raw_repository.mark_act_version_promoted(version)
            promoted += 1

        logger.info("Promoted %s act versions, skipped %s", promoted, skipped)
        return promoted

    def promote_pending_subsidiary(self, max_versions: int | None) -> int:
        pending = self.raw_repository.get_subsidiary_versions_pending_promote(max_versions)
        instrument_uris = [instrument.source_url for _, instrument, _ in pending]
        already_promoted = self.knowledge_repository.get_existing_subsidiary_uris(instrument_uris)

        logger.info(
            "Promoting %s current subsidiary legislation versions (%s already in knowledge base)",
            len(pending),
            len(already_promoted),
        )

        promoted = 0
        skipped = 0
        for act, instrument, version in pending:
            if instrument.source_url in already_promoted:
                self.raw_repository.mark_subsidiary_version_promoted(version)
                skipped += 1
                continue

            parsed = self.parser.parse_html(version.html)
            flattened = self.parser.flatten(parsed.provisions)
            if (
                parsed.parse_status != ParseStatus.COMPLETE
                or parsed.extracted_provision_count != version.extracted_provision_count
            ):
                logger.warning(
                    "Cannot promote %s: extract count %s != stored %s",
                    instrument.slug,
                    parsed.extracted_provision_count,
                    version.extracted_provision_count,
                )
                skipped += 1
                continue

            knowledge_act = self.knowledge_repository.get_or_create_act(act.source_url, act.title)
            row = self.knowledge_repository.add_subsidiary_legislation(
                knowledge_act.id,
                instrument.source_url,
                instrument.title,
                instrument.number,
                version.valid_from,
            )
            self.knowledge_repository.add_provisions(
                instrument.source_url,
                flattened,
                subsidiary_legislation_id=row.id,
            )
            self.raw_repository.mark_subsidiary_version_promoted(version)
            promoted += 1

        logger.info("Promoted %s subsidiary legislations, skipped %s", promoted, skipped)
        return promoted

    def promote_act_version(self, act: RawAct, version: RawActVersion, version_uri: str) -> bool:
        if version.html is None:
            logger.warning("Cannot promote %s %s: missing html", act.slug, version.valid_from)
            return False

        parsed = self.parser.parse_html(version.html)
        if (
            parsed.parse_status != ParseStatus.COMPLETE
            or parsed.extracted_provision_count != version.extracted_provision_count
        ):
            logger.warning(
                "Cannot promote %s %s: extract count %s != stored %s",
                act.slug,
                version.valid_from,
                parsed.extracted_provision_count,
                version.extracted_provision_count,
            )
            return False

        flattened = self.parser.flatten(parsed.provisions)
        knowledge_act = self.knowledge_repository.get_or_create_act(act.source_url, act.title)
        knowledge_version = self.knowledge_repository.add_act_version(
            knowledge_act.id,
            version_uri,
            version.valid_from,
            version.is_current,
        )
        self.knowledge_repository.add_provisions(
            version_uri,
            flattened,
            act_version_id=knowledge_version.id,
        )
        if version.is_current:
            self.knowledge_repository.replace_definitions(knowledge_act.id, parsed.definitions)

        return True

    @staticmethod
    def act_version_uri(act: RawAct, version: RawActVersion) -> str:
        return f"{act.source_url.rstrip('/')}/{version.valid_from:%Y%m%d}"


class LegislationRawPromoter:
    @staticmethod
    def run(max_versions: int | None = None, include_subsidiary: bool = True) -> None:
        raw_maker = get_raw_source_session_maker()
        knowledge_maker = get_knowledge_base_session_maker()

        with raw_maker() as raw_session, knowledge_maker() as knowledge_session:
            raw_repository = RawLegislationRepository(raw_session)
            promoter = LegislationPromoter(
                raw_repository=raw_repository,
                knowledge_repository=KnowledgeLegislationRepository(knowledge_session),
                parser=LegislationDocumentParser(raw_repository),
            )
            promoted_acts = promoter.promote_pending(max_versions)
            promoted_subsidiary = 0
            if include_subsidiary:
                promoted_subsidiary = promoter.promote_pending_subsidiary(max_versions)
            knowledge_session.commit()
            raw_session.commit()

        logger.info(
            "Promote finished: %s act versions, %s subsidiary legislations",
            promoted_acts,
            promoted_subsidiary,
        )
