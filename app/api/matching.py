import io
import re
from pathlib import Path
from typing import Literal

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException ,UploadFile
from fastapi.responses import StreamingResponse

from app.schemas.matching import ErpRow, MatchRequest, MatchResponse
from app.services.match_service import match_rows

router = APIRouter()
MAX_ROWS = 5000
COLUMNS = {
    "INPUTROWID": "input_row_id",
    "MANUFACTURERNAME": "manufacturer_name",
    "MANUFACTURERPARTNUMBER": "manufacturer_part_number",
    "ITEMDESCRIPTION": "item_description",
    "UPC": "upc",
}

def strip_evidence(response: MatchResponse) -> None:
    """Remove the evidence lists from every candidate (include_evidence=false)."""
    for result in response.results:
        for cand in result.candidates:
            cand.evidence = []

def read_upload(file: UploadFile) -> pd.DataFrame:
    """Read an uploaded .xlsx/.csv into a DataFrame of strings; 400 on a bad type or row count."""
    suffix = Path(file.filename or "").suffix.lower()
    data = io.BytesIO(file.file.read())
    if suffix == ".csv":
        df = pd.read_csv(data, dtype=str)
    elif suffix == ".xlsx":
        df = pd.read_excel(data, dtype=str)
    else:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use .xlsx or .csv")
    if df.empty or len(df) > MAX_ROWS:
        raise HTTPException(400, f"File must have 1 to {MAX_ROWS} rows, got {len(df)}")
    return df

def to_rows(df: pd.DataFrame) -> list[ErpRow]:
    """'Manufacturer Part Number', 'MANUFACTURER_PART_NUMBER', ... -> ErpRow fields."""
    fields = {c: COLUMNS[re.sub(r"[^A-Z]", "", c.upper())] for c in df.columns
              if re.sub(r"[^A-Z]", "", c.upper()) in COLUMNS}
    data = df[list(fields)].rename(columns=fields)
    data = data.astype(object).where(data.notna(), None)
    if "input_row_id" not in data:
        data["input_row_id"] = [str(i + 1) for i in range(len(data))]
    return [ErpRow(**record) for record in data.to_dict("records")]

def to_excel(df: pd.DataFrame, response: MatchResponse) -> StreamingResponse:
    """Original columns + the match result for each row."""
    out = df.copy()
    out["PREDICTED_UNILOG_ITEM_ID"] = [r.predicted_item_id for r in response.results]
    out["CONFIDENCE"] = [r.confidence for r in response.results]
    out["BAND"] = [r.band for r in response.results]
    top = [r.candidates[0] if r.candidates else None for r in response.results]
    out["TOP_CANDIDATE"] = [f"{c.item_id} {c.mpn} {c.manufacturer}" if c else "" for c in top]
    out["EVIDENCE"] = [" | ".join(c.evidence) if c else "" for c in top]

    buffer = io.BytesIO()
    out.to_excel(buffer, index=False)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=match_results.xlsx"},
    )

@router.post("/match", response_model=MatchResponse)
def match_json(request: MatchRequest):
    """Match ERP rows sent as JSON."""
    response = match_rows(request.rows, request.options.top_k)
    if not request.options.include_evidence:
        strip_evidence(response)
    return response

@router.post("/match/file", response_model=None)
def match_file(
    file: UploadFile = File(...),
    top_k: int = Form(3, ge=1, le=20),
    include_evidence: bool = Form(True),
    output_format: Literal["json", "xlsx"] = Form("json"),
):
    """Match ERP rows from an uploaded Excel/CSV file; returns JSON or an .xlsx download."""
    df = read_upload(file)
    response = match_rows(to_rows(df), top_k)
    if not include_evidence:
        strip_evidence(response)
    return to_excel(df, response) if output_format == "xlsx" else response