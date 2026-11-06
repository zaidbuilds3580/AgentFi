"""
SQLAlchemy model for SME businesses (tenants).
"""

import uuid
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base
from backend.models.constants import (
    DEFAULT_COUNTRY,
    DEFAULT_CURRENCY,
    DEFAULT_FISCAL_YEAR_START_MONTH,
    DEFAULT_TIMEZONE,
)


class Business(Base):
    __tablename__ = "businesses"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )
    name = Column(String(255), nullable=False)
    legal_name = Column(String(255), nullable=True)
    industry = Column(String(100), nullable=True)
    country = Column(String(2), nullable=False, server_default=DEFAULT_COUNTRY)
    currency = Column(String(3), nullable=False, server_default=DEFAULT_CURRENCY)
    timezone = Column(String(50), nullable=False, server_default=DEFAULT_TIMEZONE)
    fiscal_year_start_month = Column(
        Integer,
        nullable=False,
        server_default=str(DEFAULT_FISCAL_YEAR_START_MONTH),
    )
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
            "fiscal_year_start_month BETWEEN 1 AND 12",
            name="ck_businesses_fiscal_year_start_month",
        ),
    )

    # Relationships
    users = relationship("User", back_populates="business", cascade="all, delete-orphan")
    accounts = relationship("Account", back_populates="business", cascade="all, delete-orphan")
    customers = relationship("Customer", back_populates="business", cascade="all, delete-orphan")
    vendors = relationship("Vendor", back_populates="business", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="business", cascade="all, delete-orphan")
    invoices = relationship("Invoice", back_populates="business", cascade="all, delete-orphan")
    expenses = relationship("Expense", back_populates="business", cascade="all, delete-orphan")
