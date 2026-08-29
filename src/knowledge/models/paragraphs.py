from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase

EMBEDDING_DIMENSIONS = 1536


class Paragraph(KnowledgeBase):
    __tablename__ = "paragraphs"

    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    functional_role_id: Mapped[int | None] = mapped_column(ForeignKey("functional_roles.id", ondelete="SET NULL"))
    uri: Mapped[str] = mapped_column(String, unique=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
