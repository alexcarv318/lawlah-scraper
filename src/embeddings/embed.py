from time import sleep

import tiktoken
from openai import APIConnectionError, APIError, OpenAI, RateLimitError
from sqlalchemy.orm import Session
from tiktoken import Encoding

from src.database import get_knowledge_base_session_maker
from src.embeddings.schema import EmbeddingLimits, EmbeddingUnit
from src.knowledge.models.provisions import Provision, ProvisionKind
from src.knowledge.repository import (
    KnowledgeCaseRepository,
    KnowledgeLegislationRepository,
)
from src.logger import get_logger
from src.settings import get_settings

logger = get_logger(__name__)


class KnowledgeEmbedder:
    def __init__(self, limits: EmbeddingLimits | None = None) -> None:
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not set")

        self.limits = limits or EmbeddingLimits()
        self.model = settings.embedding_model
        self.client = OpenAI(api_key=settings.openai_api_key)

        try:
            self.encoding: Encoding = tiktoken.encoding_for_model(settings.embedding_model)
        except KeyError:
            self.encoding = tiktoken.get_encoding("cl100k_base")

    def run(
        self,
        max_paragraphs: int | None = None,
        max_provisions: int | None = None,
    ) -> None:
        session_maker = get_knowledge_base_session_maker()
        with session_maker() as session:
            case_repository = KnowledgeCaseRepository(session)
            legislation_repository = KnowledgeLegislationRepository(session)

            embedded_paragraphs = self.embed_paragraphs(case_repository, session, max_paragraphs)
            embedded_provisions = self.embed_provisions(legislation_repository, session, max_provisions)
            session.commit()

        logger.info(
            "Embedding finished: %s paragraphs, %s provisions",
            embedded_paragraphs,
            embedded_provisions,
        )

    def embed_paragraphs(
        self,
        repository: KnowledgeCaseRepository,
        session: Session,
        limit: int | None,
    ) -> int:
        pending = [
            paragraph
            for paragraph in repository.get_paragraphs_pending_embedding(limit)
            if paragraph.content.strip()
        ]
        logger.info("Embedding %s paragraphs", len(pending))

        embedded = 0
        for batch_start in range(0, len(pending), self.limits.batch_size):
            batch = pending[batch_start : batch_start + self.limits.batch_size]
            vectors = self.embed_texts([paragraph.content for paragraph in batch])
            for paragraph, vector in zip(batch, vectors, strict=True):
                repository.save_paragraph_embedding(paragraph, vector)
                embedded += 1
            session.commit()
            logger.info("Embedded %s of %s paragraphs", embedded, len(pending))

        return embedded

    def embed_provisions(
        self,
        repository: KnowledgeLegislationRepository,
        session: Session,
        limit: int | None,
    ) -> int:
        trees = repository.get_current_provision_trees()
        nodes_by_id = {node.id: node for tree in trees for node in tree}
        units = self.pending_provision_units(trees)

        if limit is not None:
            units = units[:limit]

        logger.info("Embedding %s provision units", len(units))

        embedded = 0
        for batch_start in range(0, len(units), self.limits.batch_size):
            batch = units[batch_start : batch_start + self.limits.batch_size]
            vectors = self.embed_texts([unit.text for unit in batch])

            for unit, vector in zip(batch, vectors, strict=True):
                repository.save_provision_embedding(
                    nodes_by_id[unit.provision_id],
                    vector,
                    unit.text,
                )
                embedded += 1

            session.commit()

            logger.info("Embedded %s of %s provision units", embedded, len(units))

        return embedded

    def pending_provision_units(self, trees: list[list[Provision]]) -> list[EmbeddingUnit]:
        units: list[EmbeddingUnit] = []
        for nodes in trees:
            by_id = {node.id: node for node in nodes}
            for root in KnowledgeEmbedder.section_roots(nodes, by_id):
                if root.embedding is not None:
                    continue
                units.extend(self.units_from_node(root, nodes))
        return units

    def units_from_node(
        self,
        node: Provision,
        nodes: list[Provision],
    ) -> list[EmbeddingUnit]:
        if node.embedding is not None:
            return []

        text = KnowledgeEmbedder.render_subtree(node, nodes)
        if not text:
            return []

        tokens = len(self.encoding.encode(text))
        if tokens <= self.limits.max_tokens:
            return [
                EmbeddingUnit(provision_id=node.id, citation=node.citation, text=text)
            ]

        children = [child for child in nodes if child.parent_id == node.id]
        if not children:
            logger.warning(
                "Cannot embed %s (%s): %s tokens and no children",
                node.citation,
                node.uri,
                tokens,
            )
            return []

        units: list[EmbeddingUnit] = []
        for child in children:
            units.extend(self.units_from_node(child, nodes))

        if units:
            logger.info(
                "Splitting %s (%s tokens) into %s child units",
                node.citation,
                tokens,
                len(units),
            )
        return units

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        attempt = 0
        while True:
            try:
                response = self.client.embeddings.create(model=self.model, input=texts)
                return [item.embedding for item in response.data]
            except (APIError, APIConnectionError, RateLimitError) as error:
                attempt += 1
                if attempt >= self.limits.retry_limit:
                    raise
                wait_seconds = self.limits.retry_backoff_seconds * (2 ** (attempt - 1))
                logger.warning(
                    "OpenAI embeddings failed, retrying in %s seconds: %s",
                    wait_seconds,
                    error,
                )
                sleep(wait_seconds)

    @staticmethod
    def section_roots(nodes: list[Provision], by_id: dict[int, Provision]) -> list[Provision]:
        roots: list[Provision] = []
        for node in nodes:
            if node.kind != ProvisionKind.SECTION:
                continue
            if node.parent_id is None:
                roots.append(node)
                continue
            parent = by_id.get(node.parent_id)
            if parent is None or parent.kind != ProvisionKind.SCHEDULE:
                roots.append(node)
        return roots

    @staticmethod
    def render_subtree(root: Provision, nodes: list[Provision]) -> str:
        end = root.ordinal + root.descendant_count
        blocks: list[str] = []
        for node in nodes:
            if node.ordinal < root.ordinal or node.ordinal > end:
                continue
            rendered = KnowledgeEmbedder.render_node(node)
            if rendered:
                blocks.append(rendered)
        return "\n\n".join(blocks)

    @staticmethod
    def render_node(node: Provision) -> str:
        title_parts: list[str] = []
        if node.citation:
            title_parts.append(node.citation)
        if node.heading:
            title_parts.append(node.heading)
        title = " ".join(title_parts)
        if title and node.content:
            return f"{title}\n{node.content}"
        return title or node.content or ""
