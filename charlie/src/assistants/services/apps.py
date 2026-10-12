"""FastAPI app per service. Phase 0: health endpoints only; later phases add routes."""

from __future__ import annotations

from fastapi import FastAPI


def base_app(name: str) -> FastAPI:
    app = FastAPI(title=name, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"service": name, "status": "ok"}

    return app


def build_app(name: str) -> FastAPI:
    return base_app(name)
