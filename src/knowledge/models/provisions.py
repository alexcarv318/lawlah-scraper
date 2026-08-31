from enum import StrEnum

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.database import KnowledgeBase

EMBEDDING_DIMENSIONS = 1536


class ProvisionKind(StrEnum):
    PART = "part"
    DIVISION = "division"
    SUBDIVISION = "subdivision"
    SECTION = "section"
    SUBSECTION = "subsection"
    PROVISO = "proviso"
    POINT = "point"
    OPENING = "opening"
    SCHEDULE = "schedule"


class Provision(KnowledgeBase):
    __tablename__ = "provisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    act_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("act_versions.id", ondelete="CASCADE"),
        index=True,
    )
    subsidiary_legislation_id: Mapped[int | None] = mapped_column(
        ForeignKey("subsidiary_legislations.id", ondelete="CASCADE"),
        index=True,
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("provisions.id", ondelete="CASCADE"),
        index=True,
    )
    functional_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("functional_roles.id", ondelete="SET NULL")
    )
    uri: Mapped[str] = mapped_column(String, unique=True)
    kind: Mapped[ProvisionKind] = mapped_column(
        Enum(
            ProvisionKind,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    level: Mapped[int] = mapped_column(Integer)
    citation: Mapped[str | None] = mapped_column(String)
    heading: Mapped[str | None] = mapped_column(String)
    content: Mapped[str | None] = mapped_column(Text)
    amendment_note: Mapped[str | None] = mapped_column(Text)
    descendant_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    embedding_text: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))

    __table_args__ = (
        CheckConstraint(
            "(act_version_id IS NOT NULL AND subsidiary_legislation_id IS NULL)"
            " OR (act_version_id IS NULL AND subsidiary_legislation_id IS NOT NULL)",
            name="parent_document",
        ),
    )