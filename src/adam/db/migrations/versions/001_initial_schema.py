"""Initial schema.

Revision ID: 001
Revises:
Create Date: 2026-03-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Projects
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
        sa.Column("specification", postgresql.JSON, server_default="{}"),
        sa.Column("tech_stack", postgresql.JSON, server_default="{}"),
        sa.Column("architecture", postgresql.JSON, server_default="{}"),
        sa.Column("conventions", postgresql.JSON, server_default="{}"),
        sa.Column("status", sa.String(50), server_default="bootstrapping"),
        sa.Column("root_path", sa.String(1000), server_default="."),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Modules
    op.create_table(
        "modules",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, server_default="0"),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("purpose", sa.Text, server_default=""),
        sa.Column("dependencies", postgresql.JSON, server_default="[]"),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("test_coverage", sa.Float, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Files
    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "module_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer, server_default="0"),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("purpose", sa.Text, server_default=""),
        sa.Column("language", sa.String(100), server_default=""),
        sa.Column("interface_spec", postgresql.JSON, server_default="{}"),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("quality_scores", postgresql.JSON, server_default="{}"),
        sa.Column("content_hash", sa.String(64), server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # File dependencies
    op.create_table(
        "file_dependencies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "target_file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dependency_type", sa.String(50), nullable=False),
        sa.Column("description", sa.Text, server_default=""),
    )

    # Tests
    op.create_table(
        "tests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("path", sa.String(1000), nullable=False),
        sa.Column("test_type", sa.String(50), server_default="unit"),
        sa.Column("target_files", postgresql.JSON, server_default="[]"),
        sa.Column("target_modules", postgresql.JSON, server_default="[]"),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("last_output", sa.Text, server_default=""),
        sa.Column("failure_diagnosis", sa.Text, server_default=""),
        sa.Column("failure_classification", sa.String(100), server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Obligations
    op.create_table(
        "obligations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("source", sa.String(100), server_default="spec"),
        sa.Column("priority", sa.String(50), server_default="normal"),
        sa.Column("status", sa.String(50), server_default="open"),
        sa.Column("implementing_files", postgresql.JSON, server_default="[]"),
        sa.Column("testing_files", postgresql.JSON, server_default="[]"),
        sa.Column("blocked_by", postgresql.JSON, server_default="[]"),
        sa.Column("notes", sa.Text, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Validation results
    op.create_table(
        "validation_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "module_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("validator_type", sa.String(100), nullable=False),
        sa.Column("is_hard", sa.Boolean, server_default="true"),
        sa.Column("passed", sa.Boolean, nullable=True),
        sa.Column("score", sa.Float, nullable=True),
        sa.Column("diagnosis", sa.Text, server_default=""),
        sa.Column("evidence", postgresql.JSON, server_default="[]"),
        sa.Column("file_references", postgresql.JSON, server_default="[]"),
        sa.Column("confidence", sa.Float, server_default="1.0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Repair actions
    op.create_table(
        "repair_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("target_dimension", sa.String(100), nullable=False),
        sa.Column("instruction", sa.Text, nullable=False),
        sa.Column(
            "preserve_constraints", postgresql.JSON, server_default="[]"
        ),
        sa.Column(
            "allowed_interventions", postgresql.JSON, server_default="[]"
        ),
        sa.Column(
            "banned_interventions", postgresql.JSON, server_default="[]"
        ),
        sa.Column("status", sa.String(50), server_default="planned"),
        sa.Column("result_summary", sa.Text, server_default=""),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Score vectors
    op.create_table(
        "score_vectors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "file_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "module_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("modules.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("hard_pass", sa.Boolean, server_default="true"),
        sa.Column("code_readability", sa.Float, server_default="0.5"),
        sa.Column("maintainability", sa.Float, server_default="0.5"),
        sa.Column("idiomaticity", sa.Float, server_default="0.5"),
        sa.Column("security", sa.Float, server_default="0.5"),
        sa.Column("performance", sa.Float, server_default="0.5"),
        sa.Column("accessibility", sa.Float, server_default="0.5"),
        sa.Column("visual_fidelity", sa.Float, server_default="0.5"),
        sa.Column("test_coverage", sa.Float, server_default="0.5"),
        sa.Column("error_handling", sa.Float, server_default="0.5"),
        sa.Column("composite", sa.Float, server_default="0.5"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )

    # Events (append-only log)
    op.create_table(
        "events",
        sa.Column(
            "id", sa.BigInteger, primary_key=True, autoincrement=True
        ),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger, nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("payload", postgresql.JSON, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_events_project_seq", "events", ["project_id", "sequence"]
    )
    op.create_index(
        "ix_events_entity",
        "events",
        ["project_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_table("events")
    op.drop_table("score_vectors")
    op.drop_table("repair_actions")
    op.drop_table("validation_results")
    op.drop_table("obligations")
    op.drop_table("tests")
    op.drop_table("file_dependencies")
    op.drop_table("files")
    op.drop_table("modules")
    op.drop_table("projects")
