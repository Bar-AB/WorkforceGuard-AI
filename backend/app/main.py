from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import create_async_engine

from app.api import corrections, employees, findings, health, scans
from app.api.errors import register_error_handlers
from app.config import ApiSettings
from app.graph.checkpointer import open_checkpointer
from app.graph.correction_graph import build_correction_graph
from app.llm.provider import open_ollama


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = ApiSettings()
    engine = create_async_engine(settings.database_url)
    app.state.engine = engine
    try:
        async with (
            open_checkpointer(settings.database_url) as checkpointer,
            open_ollama(settings) as provider,
        ):
            app.state.correction_graph = build_correction_graph(checkpointer)
            app.state.llm_provider = provider
            app.state.explain_limit = settings.llm_explain_limit
            app.state.suggest_timeout_seconds = settings.llm_suggest_timeout_seconds
            yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="WorkforceGuard AI", lifespan=_lifespan)
    register_error_handlers(app)
    for module in (health, scans, findings, employees, corrections):
        app.include_router(module.router)
    return app


app = create_app()
