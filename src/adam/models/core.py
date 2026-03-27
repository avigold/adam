"""Core structural models: Project, Module, File, FileDependency."""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from adam.models.base import Base, TimestampMixin, UUIDMixin


class Project(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(Text, default="")
    specification: Mapped[dict] = mapped_column(JSON, default=dict)
    tech_stack: Mapped[dict] = mapped_column(JSON, default=dict)
    architecture: Mapped[dict] = mapped_column(JSON, default=dict)
    conventions: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="bootstrapping")
    root_path: Mapped[str] = mapped_column(String(1000), default=".")

    modules: Mapped[list[Module]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="Module.ordinal"
    )
    obligations: Mapped[list[Obligation]] = relationship(  # noqa: F821
        back_populates="project", cascade="all, delete-orphan"
    )


class Module(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "modules"

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("projects.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    name: Mapped[str] = mapped_column(String(500))
    purpose: Mapped[str] = mapped_column(Text, default="")
    dependencies: Mapped[dict] = mapped_column(JSON, default=list)  # list of module names
    status: Mapped[str] = mapped_column(String(50), default="pending")
    test_coverage: Mapped[float | None] = mapped_column(default=None)

    project: Mapped[Project] = relationship(back_populates="modules")
    files: Mapped[list[File]] = relationship(
        back_populates="module", cascade="all, delete-orphan", order_by="File.ordinal"
    )


class File(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "files"

    module_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("modules.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    path: Mapped[str] = mapped_column(String(1000))
    purpose: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(100), default="")
    interface_spec: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(50), default="pending")
    quality_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    content_hash: Mapped[str] = mapped_column(String(64), default="")

    module: Mapped[Module] = relationship(back_populates="files")
    outgoing_deps: Mapped[list[FileDependency]] = relationship(
        foreign_keys="FileDependency.source_file_id",
        back_populates="source_file",
        cascade="all, delete-orphan",
    )
    incoming_deps: Mapped[list[FileDependency]] = relationship(
        foreign_keys="FileDependency.target_file_id",
        back_populates="target_file",
        cascade="all, delete-orphan",
    )


class FileDependency(UUIDMixin, Base):
    __tablename__ = "file_dependencies"

    source_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("files.id", ondelete="CASCADE")
    )
    target_file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(), ForeignKey("files.id", ondelete="CASCADE")
    )
    dependency_type: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(Text, default="")

    source_file: Mapped[File] = relationship(foreign_keys=[source_file_id])
    target_file: Mapped[File] = relationship(foreign_keys=[target_file_id])
