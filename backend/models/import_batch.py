"""
SQLAlchemy model for import batches (auditable file ingest records).
"""

import uuid
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base
from backend.models.constants import (
    DEFAULT_SOURCE_DATASET,
    IMPORT_STATUS_PROCESSING,
    IMPORT_STATUSES,
    IMPORT_TYPES,
    ON_ERROR_MODE_SKIP,
    ON_ERROR_MODES,
)


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_by = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    import_type = Column(String(30), nullable=False)
    source_dataset = Column(
        Text,
        nullable=False,
        server_default=text(f"'{DEFAULT_SOURCE_DATASET}'"),
    )
    original_filename = Column(Text, nullable=True)
    file_size_bytes = Column(Integer, nullable=True)
    file_sha256 = Column(String(64), nullable=True)
    status = Column(
        String(30),
        nullable=False,
        server_default=text(f"'{IMPORT_STATUS_PROCESSING}'"),
    )
    on_error_mode = Column(
        String(10),
        nullable=False,
        server_default=text(f"'{ON_ERROR_MODE_SKIP}'"),
    )
    total_rows = Column(Integer, nullable=False, server_default=text("0"))
    inserted_rows = Column(Integer, nullable=False, server_default=text("0"))
    duplicate_rows = Column(Integer, nullable=False, server_default=text("0"))
    error_rows = Column(Integer, nullable=False, server_default=text("0"))
    error_summary = Column(JSONB, nullable=True)

    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    business = relationship("Business")
    creator = relationship("User")
    errors = relationship(
        "ImportRowError",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ImportRowError.row_number",
    )

    __table_args__ = (
        CheckConstraint(
            f"import_type IN {tuple(IMPORT_TYPES)}",
            name="ck_import_batches_import_type",
        ),
        CheckConstraint(
            f"status IN {tuple(IMPORT_STATUSES)}",
            name="ck_import_batches_status",
        ),
        CheckConstraint(
            f"on_error_mode IN {tuple(ON_ERROR_MODES)}",
            name="ck_import_batches_on_error_mode",
        ),
        Index(
            "ix_import_batches_business_created",
            "business_id",
            created_at.desc(),
        ),
        Index(
            "uq_import_batches_dedup",
            "business_id",
            "import_type",
            "file_sha256",
            unique=True,
            postgresql_where=text(
                "status IN ('completed', 'completed_with_errors')"
            ),
        ),
    )
