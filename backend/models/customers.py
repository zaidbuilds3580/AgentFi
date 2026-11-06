"""
SQLAlchemy model for customers of an SME business.
"""

import uuid
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base


class Customer(Base):
    __tablename__ = "customers"

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
    name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    gst_number = Column(String(50), nullable=True)
    payment_terms_days = Column(Integer, nullable=False, server_default="30")
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
        Index("ix_customers_business_id_name", "business_id", "name"),
    )

    business = relationship("Business", back_populates="customers")
    invoices = relationship("Invoice", back_populates="customer")
