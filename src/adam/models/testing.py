"""Test model — tracks test files and their execution state."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adam.models.base import Base, TimestampMixin, UUIDMixin
from adam.models.core import File


class Test(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "tests"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE")
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    path: Mapped[str] = mapped_column(String(1000))
    test_type: Mapped[str] = mapped_column(String(50), default="unit")
    target_files: Mapped[list] = mapped_column(JSON, default=list)  # list of file paths
    target_modules: Mapped[list] = mapped_column(JSON, default=list)  # list of module names
    status: Mapped[str] = mapped_column(String(50), default="pending")
    last_output: Mapped[str] = mapped_column(Text, default="")
    failure_diagnosis: Mapped[str] = mapped_column(Text, default="")
    failure_classification: Mapped[str] = mapped_column(String(100), default="")

    file: Mapped[File | None] = relationship(foreign_keys=[file_id])
