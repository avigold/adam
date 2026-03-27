"""Obligation ledger — tracks what was specified vs implemented vs tested."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adam.models.base import Base, TimestampMixin, UUIDMixin
from adam.models.core import Project


class Obligation(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "obligations"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE")
    )
    description: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(100), default="spec")  # spec, user, architect
    priority: Mapped[str] = mapped_column(String(50), default="normal")
    status: Mapped[str] = mapped_column(String(50), default="open")
    implementing_files: Mapped[list] = mapped_column(JSON, default=list)  # file paths
    testing_files: Mapped[list] = mapped_column(JSON, default=list)  # test paths
    blocked_by: Mapped[list] = mapped_column(JSON, default=list)  # obligation IDs
    notes: Mapped[str] = mapped_column(Text, default="")

    project: Mapped[Project] = relationship(back_populates="obligations")
