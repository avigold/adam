"""Analytics models: validation results, repair actions, score vectors."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from adam.models.base import Base, UUIDMixin


class ValidationResultRecord(UUIDMixin, Base):
    __tablename__ = "validation_results"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE")
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("modules.id", ondelete="SET NULL"), nullable=True
    )
    validator_type: Mapped[str] = mapped_column(String(100))
    is_hard: Mapped[bool] = mapped_column(default=True)
    passed: Mapped[bool | None] = mapped_column(nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    diagnosis: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=list)
    file_references: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class RepairActionRecord(UUIDMixin, Base):
    __tablename__ = "repair_actions"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE")
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    priority: Mapped[int] = mapped_column(Integer)
    target_dimension: Mapped[str] = mapped_column(String(100))
    instruction: Mapped[str] = mapped_column(Text)
    preserve_constraints: Mapped[list] = mapped_column(JSON, default=list)
    allowed_interventions: Mapped[list] = mapped_column(JSON, default=list)
    banned_interventions: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(50), default="planned")
    result_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ScoreVectorRecord(UUIDMixin, Base):
    __tablename__ = "score_vectors"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE")
    )
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    module_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(), ForeignKey("modules.id", ondelete="SET NULL"), nullable=True
    )
    hard_pass: Mapped[bool] = mapped_column(default=True)
    code_readability: Mapped[float] = mapped_column(Float, default=0.5)
    maintainability: Mapped[float] = mapped_column(Float, default=0.5)
    idiomaticity: Mapped[float] = mapped_column(Float, default=0.5)
    security: Mapped[float] = mapped_column(Float, default=0.5)
    performance: Mapped[float] = mapped_column(Float, default=0.5)
    accessibility: Mapped[float] = mapped_column(Float, default=0.5)
    visual_fidelity: Mapped[float] = mapped_column(Float, default=0.5)
    test_coverage: Mapped[float] = mapped_column(Float, default=0.5)
    error_handling: Mapped[float] = mapped_column(Float, default=0.5)
    composite: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
