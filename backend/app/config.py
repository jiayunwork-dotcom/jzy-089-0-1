from __future__ import annotations

import os


class Settings:
    database_url: str = os.getenv(
        "DATABASE_URL", "postgresql://bi_user:bi_pass@db:5432/bi_demo"
    )
    model_path: str = os.getenv("MODEL_PATH", "/data/model.json")
    # 启动时若库为空则自动建表并灌入样例数据
    auto_seed: bool = os.getenv("AUTO_SEED", "1") != "0"
    statement_timeout_ms: int = int(os.getenv("STATEMENT_TIMEOUT_MS", "15000"))
    default_limit: int = int(os.getenv("DEFAULT_LIMIT", "1000"))
    max_limit: int = int(os.getenv("MAX_LIMIT", "10000"))


settings = Settings()
