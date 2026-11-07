"""
SQLAlchemy model for customer invoices.
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
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from backend.database.session import Base
from backend.models.constants import DEFAULT_CURRENCY, INVOICE_STATUSES


class Invoice(Base):
    __tablename__ = "invoices"

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
    customer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    invoice_number = Column(String(100), nullable=False)
    invoice_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=False)
    amount_total = Column(Numeric(18, 2), nullable=False)
    amount_paid = Column(Numeric(18, 2), nullable=False, server_default="0")
    currency = Column(String(3), nullable=False, server_default=DEFAULT_CURRENCY)
    
    # NOTE in code: 'overdue' is ultimately derived by Member 3's finance engine
    # from due_date and amount_paid; the stored status is a snapshot and the engine
    # is the source of truth.
    status = Column(String(20), nullable=False, server_default="sent")
    payment_date = Column(Date, nullable=True)
    notes = Column(Text, nullable=True)

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
            "due_date >= invoice_date",
            name="ck_invoices_due_date_after_invoice_date",
        ),
        CheckConstraint(
            "amount_total >= 0",
            name="ck_invoices_amount_total_non_negative",
        ),
        CheckConstraint(
            "amount_paid >= 0 AND amount_paid <= amount_total",
            name="ck_invoices_amount_paid_valid",
        ),
        CheckConstraint(
            f"status IN {INVOICE_STATUSES}",
            name="ck_invoices_status",
        ),
        UniqueConstraint("business_id", "invoice_number", name="uq_invoices_business_id_invoice_number"),
        Index("ix_invoices_business_id_status_due_date", "business_id", "status", "due_date"),
        Index("ix_invoices_business_id_customer_id", "business_id", "customer_id"),
        # Deduplication guard for Member 3's ETL
        Index(
            "uq_invoices_provenance",
            "business_id",
            "source_dataset",
            "source_record_id",
            unique=True,
            postgresql_where=text("source_dataset IS NOT NULL AND source_record_id IS NOT NULL"),
        ),
    )

    business = relationship("Business", back_populates="invoices")
    customer = relationship("Customer", back_populates="invoices")

