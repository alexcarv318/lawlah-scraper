from datetime import date
from enum import StrEnum

from sqlalchemy import (
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.database import RawSourceBase
from src.raw.models.cases import FetchStatus, ParseStatus


class RawLegislationStatus(StrEnum):
    DISCOVERED = "discovered"
    FETCH_FAILED = "fetch_failed"
    PARSE_INCOMPLETE = "parse_incomplete"
    COMPLETE = "complete"
    NEEDS_REVIEW = "needs_review"


class RawAct(RawSourceBase):
    __tablename__ = "raw_acts"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)
    status: Mapped[RawLegislationStatus] = mapped_column(
        Enum(
            RawLegislationStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        index=True,
    )


class RawActVersion(RawSourceBase):
    __tablename__ = "raw_act_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_act_id: Mapped[int] = mapped_column(
        ForeignKey("raw_acts.id", ondelete="CASCADE"),
        index=True,
    )
    valid_from: Mapped[date] = mapped_column(Date, index=True)
    is_current: Mapped[bool]
    source_url: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    fetch_status: Mapped[FetchStatus] = mapped_column(
        Enum(
            FetchStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        index=True,
    )
    fetch_error: Mapped[str | None] = mapped_column(Text)
    html: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(
            ParseStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    expected_provision_count: Mapped[int | None] = mapped_column(Integer)
    extracted_provision_count: Mapped[int | None] = mapped_column(Integer)
    needs_review: Mapped[bool]

    __table_args__ = (
        UniqueConstraint("raw_act_id", "valid_from", name="uq_raw_act_versions_raw_act_id_valid_from"),
    )


class RawSubsidiaryLegislation(RawSourceBase):
    __tablename__ = "raw_subsidiary_legislations"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_act_id: Mapped[int] = mapped_column(
        ForeignKey("raw_acts.id", ondelete="CASCADE"),
        index=True,
    )
    slug: Mapped[str] = mapped_column(String, unique=True)
    title: Mapped[str] = mapped_column(String)
    number: Mapped[str] = mapped_column(String)
    source_url: Mapped[str] = mapped_column(Text)
    status: Mapped[RawLegislationStatus] = mapped_column(
        Enum(
            RawLegislationStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        index=True,
    )


class RawSubsidiaryLegislationVersion(RawSourceBase):
    __tablename__ = "raw_subsidiary_legislation_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_subsidiary_legislation_id: Mapped[int] = mapped_column(
        ForeignKey("raw_subsidiary_legislations.id", ondelete="CASCADE"),
    )
    valid_from: Mapped[date] = mapped_column(Date)
    is_current: Mapped[bool]
    source_url: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    fetch_status: Mapped[FetchStatus] = mapped_column(
        Enum(
            FetchStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
    )
    fetch_error: Mapped[str | None] = mapped_column(Text)
    html: Mapped[str | None] = mapped_column(Text)
    source_metadata: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    parse_status: Mapped[ParseStatus] = mapped_column(
        Enum(
            ParseStatus,
            native_enum=False,
            length=32,
            values_callable=lambda enum_class: [member.value for member in enum_class],
        )
    )
    expected_provision_count: Mapped[int | None] = mapped_column(Integer)
    extracted_provision_count: Mapped[int | None] = mapped_column(Integer)
    needs_review: Mapped[bool]

    __table_args__ = (
        UniqueConstraint(
            "raw_subsidiary_legislation_id",
            "valid_from",
            name="uq_raw_subsidiary_legislation_versions_parent_valid_from",
        ),
        Index("ix_raw_sl_versions_parent_id", "raw_subsidiary_legislation_id"),
        Index("ix_raw_sl_versions_valid_from", "valid_from"),
        Index("ix_raw_sl_versions_fetch_status", "fetch_status"),
    )
