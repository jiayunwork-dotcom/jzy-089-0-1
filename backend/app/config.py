"""Runtime configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://app_ro:app_ro_secret@db:5432/appdb"
    )
    model_path: str = os.getenv("MODEL_PATH", "/data/model.json")
    max_rows: int = int(os.getenv("MAX_ROWS", "10000"))
    # CORS is only needed for local development against the Vite dev server.
    cors_origins: str = os.getenv("CORS_ORIGINS", "http://localhost:5173")


settings = Settings()
