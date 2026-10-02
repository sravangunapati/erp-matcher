from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import health, indexing, matching
from app.services.match_service import SearchError

app = FastAPI(title= "ERP Matcher")

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(indexing.router, prefix="/api/v1", tags=["indexing"])
app.include_router(matching.router, prefix="/api/v1", tags=["matching"])


@app.exception_handler(SearchError)
def search_error(request: Request, exc: SearchError) -> JSONResponse:
    """The catalog can't be searched (not indexed yet, or Elasticsearch failed): 503 instead of wrong answers."""
    return JSONResponse(status_code=503, content={"detail": str(exc)})
