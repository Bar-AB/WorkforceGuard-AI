from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import employees, findings, health, scans
from app.api.errors import register_error_handlers
from app.config import Settings


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_async_engine(Settings().database_url)
    app.state.engine = engine
    try:
        yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="WorkforceGuard AI", lifespan=_lifespan)
    register_error_handlers(app)
    for module in (health, scans, findings, employees):
        app.include_router(module.router)
    return app


app = create_app()
