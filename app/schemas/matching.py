from typing import Literal
from pydantic import BaseModel, Field


class ErpRow(BaseModel):
    """One ERP item to match; every field except the ID is optional."""
    input_row_id: str
    manufacturer_name: str | None = None
    manufacturer_part_number: str | None = None
    item_description: str | None = None
    upc: str | None = None

class MatchOptions(BaseModel):
    """Request options: how many candidates to return and whether to include evidence."""
    top_k: int = Field(3, ge=1, le=20)
    include_evidence: bool = True

class MatchRequest(BaseModel):
    """Body of POST /match."""
    rows: list[ErpRow] = Field(..., min_length = 1, max_length = 5000)
    options: MatchOptions = Field(default_factory=MatchOptions)

class Candidate(BaseModel):
    """A catalog item considered for a row, with its probability and the reasons."""
    item_id: str
    mpn: str
    manufacturer: str
    description: str
    score: float
    evidence: list[str] = []
    source: Literal["identifier", "semantic"] = "identifier"

class MatchResult(BaseModel):
    """The decision for one row: an item ID or NO_MATCH, its confidence and workflow band."""
    input_row_id: str
    predicted_item_id: str
    confidence: float
    band: Literal["AUTO_ACCEPT", "REVIEW", "NO_MATCH"]
    candidates: list[Candidate] = []

class MatchSummary(BaseModel):
    """Row counts per band."""
    rows: int
    auto_accept: int
    review: int
    no_match: int

class MatchResponse(BaseModel):
    """Response of the matching endpoints."""
    results: list[MatchResult]
    summary: MatchSummary