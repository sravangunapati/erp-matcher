import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from app.jobs.store import create_job, get_job, update_job
from app.schemas.indexing import IndexJob
from app.services.index_service import build_index

router = APIRouter()
ALLOWED = {".xlsx", ".csv"}

def run_index_job(job_id: str, path: str) -> None:
    """Background task: build the index from the saved upload, record the outcome, delete the temp file."""
    update_job(job_id, status="running")
    try:
        update_job(job_id, status="completed", result=build_index(path))
    except Exception as e:
        update_job(job_id, status="failed", error=str(e))
    finally:
        Path(path).unlink(missing_ok=True)

@router.post("/index/jobs", response_model=IndexJob, status_code=202)
def start_index_job(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """Save the uploaded catalog to a temp file and start indexing it in the background; returns the job."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED:
        raise HTTPException(400, f"Unsupported file type '{suffix}'. Use .xlsx or .csv")
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        shutil.copyfileobj(file.file, tmp)
    job = create_job()
    background_tasks.add_task(run_index_job, job["job_id"], tmp.name)
    return job

@router.get("/index/jobs/{job_id}", response_model=IndexJob)
def get_index_job(job_id: str):
    """Status of one indexing job (404 if the ID is unknown)."""
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job