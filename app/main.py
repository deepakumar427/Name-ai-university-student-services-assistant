"""FastAPI application entry point. Run with: uvicorn app.main:app --reload"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db.database import init_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.jwt_secret_generated:
        logging.getLogger(__name__).warning(
            "JWT_SECRET is not set: using a random secret, so login tokens stop working when the API restarts.")
    yield


app = FastAPI(
    title="University Student Services Assistant",
    version="0.1.0",
    description="Answers student questions from authorised university documents and records, with citations.",
    lifespan=lifespan,
)
app.include_router(router)
