"""
SQLAlchemy model for SME operational expenses.
"""

import uuid
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base
from backend.models.constants import DEFAULT_CURRENCY, PAYMENT_METHODS


class Expense(Base):
    __tablename__ = "expenses"

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
    vendor_id = Column(
        UUID(as_uuid=True),
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expense_date = Column(Date, nullable=False)
    category = Column(String(100), nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False, server_default=DEFAULT_CURRENCY)
    description = Column(Text, nullable=True)
    payment_method = Column(String(20), nullable=True)
    is_recurring = Column(Boolean, nullable=False, server_default=text("false"))

    # Data Provenance & ETL Deduplication fields (Member 3)
    source_dataset = Column(Text, nullable=True)
    source_record_id = Column(Text, nullable=True)

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

    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_expenses_amount_positive"),
        CheckConstraint(
            f"payment_method IS NULL OR payment_method IN {PAYMENT_METHODS}",
            name="ck_expenses_payment_method",
        ),
        Index("ix_expenses_business_id_date", "business_id", "expense_date"),
        Index("ix_expenses_business_id_category", "business_id", "category"),
        Index("ix_expenses_business_id_vendor_id", "business_id", "vendor_id"),
        # Deduplication guard for Member 3's ETL
        Index(
            "uq_expenses_provenance",
            "business_id",
            "source_dataset",
            "source_record_id",
            unique=True,
            postgresql_where=text("source_dataset IS NOT NULL AND source_record_id IS NOT NULL"),
        ),
    )

    business = relationship("Business", back_populates="expenses")
    vendor = relationship("Vendor", back_populates="expenses")
    account = relationship("Account", back_populates="expenses")
    transaction = relationship("Transaction", back_populates="expenses")
