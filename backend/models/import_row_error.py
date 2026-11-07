"""
SQLAlchemy model for import row-level errors.
"""

import uuid
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base


class ImportRowError(Base):
    __tablename__ = "import_row_errors"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    batch_id = Column(
        UUID(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    business_id = Column(
        UUID(as_uuid=True),
        ForeignKey("businesses.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    row_number = Column(Integer, nullable=False)
    field = Column(Text, nullable=True)
    code = Column(Text, nullable=False)
    message = Column(Text, nullable=False)
    raw_row = Column(JSONB, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    batch = relationship("ImportBatch", back_populates="errors")
    business = relationship("Business")

    __table_args__ = (
        Index("ix_import_row_errors_batch_row", "batch_id", "row_number"),
    )
