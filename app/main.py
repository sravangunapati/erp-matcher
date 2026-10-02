from fastapi import FastAPI

from app.api import health, indexing, matching

app = FastAPI(title= "ERP Matcher")

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(indexing.router, prefix="/api/v1", tags=["indexing"])
app.include_router(matching.router, prefix="/api/v1", tags=["matching"])
