import uuid
from datetime import datetime
from threading import Lock

_jobs: dict[str, dict] = {}
_lock = Lock()

def create_job() -> dict:
    """Register a new queued job and return it; IDs look like 'idx_1a2b3c4d'."""
    job = {
        "job_id": f"idx_{uuid.uuid4().hex[:8]}",
        "status": "queued",
        "created_at": datetime.now(),
        "result": None,
        "error": None,
    }
    with _lock:
        _jobs[job["job_id"]] = job
    return job

def update_job(job_id: str, **fields) -> None:
    """Set fields on a job (status, result, error...)."""
    with _lock:
        _jobs[job_id].update(fields)

def get_job(job_id: str) -> dict | None:
    """The job with this ID, or None."""
    return _jobs.get(job_id)