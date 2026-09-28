from fastapi import FastAPI

from app.api import health


def create_app() -> FastAPI:
    app = FastAPI(title="WorkforceGuard AI")
    app.include_router(health.router)
    return app


app = create_app()
