"""SQLAlchemy ORM models for RedFlagged Azure SQL database."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class DlpAlert(Base):
    """
    One row per Purview DLP alert, keyed by the Graph API alert ID.
    Replaces each object in the db_info.txt "purview" array.
    """
    __tablename__ = "dlp_alerts"

    id:            Mapped[str]            = mapped_column(String(200), primary_key=True)
    alert_name:    Mapped[str]            = mapped_column(String(500), nullable=False)
    severity:      Mapped[str | None]     = mapped_column(String(20))
    status:        Mapped[str | None]     = mapped_column(String(50))
    time_detected: Mapped[datetime | None]= mapped_column(DateTime)
    actor:         Mapped[str | None]     = mapped_column(String(255))
    location:      Mapped[str | None]     = mapped_column(String(100))
    incident_id:   Mapped[str | None]     = mapped_column(String(50))

    # ODIN fields — populated later when AI integration is added
    odin_summary:           Mapped[str | None] = mapped_column(Text)
    odin_confidence:        Mapped[str | None] = mapped_column(String(20))
    odin_confidence_label:  Mapped[str | None] = mapped_column(String(50))
    odin_what_happened:     Mapped[str | None] = mapped_column(Text)
    odin_why_it_matters:    Mapped[str | None] = mapped_column(Text)
    odin_next_actions:      Mapped[str | None] = mapped_column(Text)
    odin_raw_response:      Mapped[str | None] = mapped_column(Text)
    odin_updated_at:        Mapped[datetime | None] = mapped_column(DateTime)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at:    Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    artifacts: Mapped[list[Artifact]] = relationship(
        "Artifact", back_populates="alert", cascade="all, delete-orphan"
    )


class Artifact(Base):
    """
    One row per artifact file captured for a DLP alert.
    Replaces each object in the artifacts_inventory.json "files" array.
    """
    __tablename__ = "artifacts"

    id:            Mapped[int]            = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id:      Mapped[str]            = mapped_column(String(200), ForeignKey("dlp_alerts.id"))
    file_name:     Mapped[str | None]     = mapped_column(String(255))
    relative_path: Mapped[str | None]     = mapped_column(String(500))
    folder_name:   Mapped[str | None]     = mapped_column(String(500))
    modified_at:   Mapped[datetime | None]= mapped_column(DateTime)
    size_bytes:    Mapped[int | None]     = mapped_column(BigInteger)
    deletable:     Mapped[bool]           = mapped_column(Boolean, default=False)
    created_at:    Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow)

    alert: Mapped[DlpAlert] = relationship("DlpAlert", back_populates="artifacts")
