"""import batches and row errors

Revision ID: 0002_import_batches
Revises: 0001_canonical_schema
Create Date: 2026-11-07 19:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002_import_batches"
down_revision: Union[str, None] = "0001_canonical_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. import_batches table
    op.create_table(
        "import_batches",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("import_type", sa.String(length=30), nullable=False),
        sa.Column(
            "source_dataset",
            sa.Text(),
            server_default="csv_upload",
            nullable=False,
        ),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=True),
        sa.Column("file_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="processing",
            nullable=False,
        ),
        sa.Column(
            "on_error_mode",
            sa.String(length=10),
            server_default="skip",
            nullable=False,
        ),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("inserted_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_import_batches_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_import_batches_created_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_batches"),
        sa.CheckConstraint(
            "import_type IN ('transactions', 'invoices', 'expenses')",
            name="ck_import_batches_import_type",
        ),
        sa.CheckConstraint(
            "status IN ('processing', 'completed', 'completed_with_errors', 'failed')",
            name="ck_import_batches_status",
        ),
        sa.CheckConstraint(
            "on_error_mode IN ('skip', 'abort')",
            name="ck_import_batches_on_error_mode",
        ),
    )

    op.create_index(
        "ix_import_batches_business_created",
        "import_batches",
        ["business_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_import_batches_business_id",
        "import_batches",
        ["business_id"],
    )
    op.create_index(
        "ix_import_batches_created_by",
        "import_batches",
        ["created_by"],
    )
    op.create_index(
        "uq_import_batches_dedup",
        "import_batches",
        ["business_id", "import_type", "file_sha256"],
        unique=True,
        postgresql_where=sa.text("status IN ('completed', 'completed_with_errors')"),
    )

    # 2. import_row_errors table
    op.create_table(
        "import_row_errors",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("business_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("field", sa.Text(), nullable=True),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("raw_row", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            ["import_batches.id"],
            name="fk_import_row_errors_batch_id_import_batches",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name="fk_import_row_errors_business_id_businesses",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_import_row_errors"),
    )

    op.create_index(
        "ix_import_row_errors_batch_id",
        "import_row_errors",
        ["batch_id"],
    )
    op.create_index(
        "ix_import_row_errors_business_id",
        "import_row_errors",
        ["business_id"],
    )
    op.create_index(
        "ix_import_row_errors_batch_row",
        "import_row_errors",
        ["batch_id", "row_number"],
    )

    # Enable Row Level Security (RLS) on both tables
    op.execute("ALTER TABLE import_batches ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE import_row_errors ENABLE ROW LEVEL SECURITY;")


def downgrade() -> None:
    op.drop_table("import_row_errors")
    op.drop_table("import_batches")
