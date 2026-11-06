"""
SQLAlchemy model for financial cashflow transactions (inflow/outflow).
"""

import uuid
from sqlalchemy import (
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
from backend.models.constants import DEFAULT_CURRENCY, TRANSACTION_TYPES


class Transaction(Base):
    __tablename__ = "transactions"

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
    account_id = Column(
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    transaction_date = Column(Date, nullable=False)
    transaction_type = Column(String(10), nullable=False)
    # Amounts are strictly stored POSITIVE; inflow/outflow direction comes from transaction_type
    amount = Column(Numeric(18, 2), nullable=False)
    currency = Column(String(3), nullable=False, server_default=DEFAULT_CURRENCY)
    description = Column(Text, nullable=True)
    category = Column(String(100), nullable=True)
    counterparty = Column(Text, nullable=True)
    reference = Column(String(100), nullable=True)

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
        CheckConstraint(
            f"transaction_type IN {TRANSACTION_TYPES}",
            name="ck_transactions_transaction_type",
        ),
        CheckConstraint("amount > 0", name="ck_transactions_amount_positive"),
        Index("ix_transactions_business_id_date", "business_id", "transaction_date"),
        Index("ix_transactions_business_id_category", "business_id", "category"),
        Index("ix_transactions_account_id_date", "account_id", "transaction_date"),
        # Deduplication guard for Member 3's ETL
        Index(
            "uq_transactions_provenance",
            "business_id",
            "source_dataset",
            "source_record_id",
            unique=True,
            postgresql_where=text("source_dataset IS NOT NULL AND source_record_id IS NOT NULL"),
        ),
    )

    business = relationship("Business", back_populates="transactions")
    account = relationship("Account", back_populates="transactions")
    expenses = relationship("Expense", back_populates="transaction")
