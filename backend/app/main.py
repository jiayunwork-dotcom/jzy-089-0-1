"""FastAPI 入口：装配模型存储、数据库、路由、静态前端。"""
from __future__ import annotations

import logging
import os
import pathlib

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .config import settings
from .db import get_admin_conn, get_conn
from .model_store import ModelStore
from .routers.query import router as query_router

logger = logging.getLogger("bi")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

FRONTEND_DIST = pathlib.Path(
    os.getenv("FRONTEND_DIST", "/app/frontend_dist")
)


def create_app() -> FastAPI:
    app = FastAPI(title="拖拽式自助数据查询", version="1.0.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
    )

    store = ModelStore(settings.model_path)
    app.state.store = store
    app.state.settings = settings
    app.state.db = get_conn
    app.state.admin_db = get_admin_conn

    # 首次启动：落盘样例模型 + 建样例库灌数
    if settings.auto_seed:
        _bootstrap(store)

    app.include_router(query_router)

    if FRONTEND_DIST.exists():
        app.mount(
            "/",
            StaticFiles(directory=str(FRONTEND_DIST), html=True),
            name="frontend",
        )
    else:
        @app.get("/")
        def _no_frontend():
            return {"message": "API 已就绪；未找到前端构建产物（FRONTEND_DIST）。"}

    return app


def _bootstrap(store: ModelStore) -> None:
    from .sample_model import build_sample_model

    # 模型文件为空（首次启动由 ModelStore 初始化）时写入样例模型
    try:
        model = store.load()
        if not model.tables:
            store.save(build_sample_model())
    except Exception as e:
        logger.warning("模型加载失败，回退为样例模型: %s", e)
        store.save(build_sample_model())

    try:
        schema_path = pathlib.Path(__file__).resolve().parents[1] / "seed" / "schema.sql"
        with get_admin_conn() as conn:
            exists = conn.execute(
                "SELECT to_regclass('public.orders') IS NOT NULL"
            ).fetchone()[0]
            if not exists:
                conn.execute(schema_path.read_text(encoding="utf-8"))
                from seed.seed_data import seed as seed_data

                seed_data(conn)
                logger.info("样例业务库已创建并灌入数据")
    except Exception as e:  # 数据库尚未就绪不阻塞 API 启动
        logger.warning("样例库初始化跳过: %s", e)


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
