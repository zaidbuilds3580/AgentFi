"""
SQLAlchemy model for financial accounts (bank, cash, card, wallet).
"""

import uuid
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base
from backend.models.constants import ACCOUNT_TYPES, DEFAULT_CURRENCY


class Account(Base):
    __tablename__ = "accounts"

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
    name = Column(String(100), nullable=False)
    account_type = Column(String(20), nullable=False)
    institution_name = Column(String(100), nullable=True)
    # Security rule: Never store full account numbers; only the last 4 digits for verification
    account_number_last4 = Column(String(4), nullable=True)
    currency = Column(String(3), nullable=False, server_default=DEFAULT_CURRENCY)
    opening_balance = Column(Numeric(18, 2), nullable=False, server_default="0")
    current_balance = Column(Numeric(18, 2), nullable=False, server_default="0")
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

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
            f"account_type IN {ACCOUNT_TYPES}",
            name="ck_accounts_account_type",
        ),
        UniqueConstraint("business_id", "name", name="uq_accounts_business_id_name"),
    )

    business = relationship("Business", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account")
    expenses = relationship("Expense", back_populates="account")
