from datetime import datetime
from typing import Literal

from pydantic import BaseModel

class IndexResult(BaseModel):
    """Outcome of one indexing run."""
    index: str
    rows_read: int
    indexed: int
    errors: list[dict] = []
    deleted: list[str] = []

class IndexJob(BaseModel):
    """An indexing job as returned by the API."""
    job_id: str
    status: Literal["queued", "running", "completed", "failed"]
    created_at: datetime
    result: IndexResult | None = None
    error: str | None = None