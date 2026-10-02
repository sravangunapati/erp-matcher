from fastapi import APIRouter

from app.core.es_client import es

router = APIRouter()

# API to check the conenction status of elasticsearch

@router.get("/health")
def health():
    """Liveness check: the API is up and whether Elasticsearch answers a ping."""
    return {"status": "ok", "elasticsearch": es.ping()}
