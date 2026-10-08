"""FastAPI application entry point."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import models  # noqa: F401  (registers the tables)
from app.api import protected, public
from app.database import Base, engine
from app.services import storage


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(engine)  # fine for this project; real deployments would use Alembic
    storage.root()
    yield


app = FastAPI(
    title="Bulk Certificate Generator",
    version="1.0.0",
    description="Submit many recipients in one request; certificates are generated in the background.",
    lifespan=lifespan,
)
app.include_router(protected)
app.include_router(public)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
