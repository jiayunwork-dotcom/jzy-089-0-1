"""FastAPI application entry point.

Wires model storage, error rendering, CORS for the Vite dev server, the
two route modules, and static hosting of the built React panel.
"""
from __future__ import annotations

import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .errors import AppError, ExpressionError
from .routers import model_routes, query_routes
from .storage import ModelStore

STATIC_DIR = os.getenv("STATIC_DIR", "/app/static")


def create_app() -> FastAPI:
    app = FastAPI(title="DragQL — drag to SQL", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            o.strip() for o in settings.cors_origins.split(",") if o.strip()
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.store = ModelStore()
    app.state.model = app.state.store.load()

    app.include_router(model_routes.router)
    app.include_router(query_routes.router)

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        body = {"error": exc.message}
        if exc.detail is not None:
            body["detail"] = exc.detail
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception):
        # pydantic RequestValidationError is handled by FastAPI itself.
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "model": app.state.model.name}

    # Static React build (mounted last so /api/* takes precedence).
    if os.path.isdir(STATIC_DIR):
        app.mount(
            "/",
            StaticFiles(directory=STATIC_DIR, html=True),
            name="static",
        )

    return app


app = create_app()
