"""DataPilot BD - FastAPI application entry point.  Run:  uvicorn backend.main:app --reload"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import init_db
from .routers import datasets, modules


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="DataPilot BD API", version="1.0.0", lifespan=lifespan,
              description="Multi-agent AI system for data entry, validation, reporting and engineering-department administration.")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
app.include_router(datasets.router)
app.include_router(modules.router)
