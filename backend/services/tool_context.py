"""
Tool Context Specification for AI Agents & Deterministic Finance Engine.

Enforces that tenant identity (business_id) is strictly injected by the server
runtime and can NEVER be chosen, supplied, or overridden by the LLM or agent.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional
import uuid
import zoneinfo

from sqlalchemy.orm import Session

from backend.auth.schemas import TokenPayload
from backend.models.businesses import Business


@dataclass(frozen=True)
class ToolContext:
    """
    Immutable security context passed into agent tools and finance engine functions.
    Constructed exclusively on the server from verified JWT claims and the database.
    """
    business_id: str
    user_id: str
    role: str
    timezone: str
    currency: str
    as_of: date

    @property
    def business_uuid(self) -> uuid.UUID:
        """Helper to return business_id as a uuid.UUID."""
        return uuid.UUID(str(self.business_id))


def build_tool_context(
    db: Session,
    token_payload: TokenPayload,
    as_of: Optional[date] = None,
) -> ToolContext:
    """
    Builds a verified, immutable ToolContext from the authenticated user token and business record.
    Server-injected: Agents and LLMs cannot fabricate or modify this context.
    """
    biz_id_str = str(token_payload.business_id)
    currency = "INR"
    tz_name = "UTC"

    try:
        biz_uuid = uuid.UUID(biz_id_str)
        business = db.query(Business).filter(Business.id == biz_uuid).first()
        if business:
            if hasattr(business, "currency") and business.currency:
                currency = business.currency
            if hasattr(business, "timezone") and business.timezone:
                tz_name = business.timezone
    except Exception:
        pass

    # Resolve reference as_of date in the business tenant's local timezone
    if as_of is None:
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
            as_of = datetime.now(tz).date()
        except Exception:
            as_of = datetime.now(timezone.utc).date()

    return ToolContext(
        business_id=biz_id_str,
        user_id=str(token_payload.sub),
        role=str(token_payload.role),
        timezone=tz_name,
        currency=currency,
        as_of=as_of,
    )
