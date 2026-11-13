"""
Pydantic schemas for file imports, batch status, row errors, and templates.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, ConfigDict, Field


class RowErrorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    row_number: int
    field: Optional[str] = None
    code: str
    message: str


class ImportResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    batch_id: uuid.UUID
    import_type: str
    status: str
    source_dataset: str
    original_filename: Optional[str] = None
    total_rows: int = 0
    inserted_rows: int = 0
    duplicate_rows: int = 0
    error_rows: int = 0
    warnings: List[str] = Field(default_factory=list)
    errors: List[RowErrorRead] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ImportBatchListResponse(BaseModel):
    items: List[ImportResult]
    total: int
    limit: int
    offset: int


class TemplateColumn(BaseModel):
    name: str
    required: bool
    type: str = "string"
    description: str
    aliases: List[str] = Field(default_factory=list)


class ImportTemplateResponse(BaseModel):
    import_type: str
    columns: List[TemplateColumn]
    example_row: Dict[str, Any]
