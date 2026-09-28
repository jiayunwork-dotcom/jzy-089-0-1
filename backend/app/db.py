"""PostgreSQL 连接管理（查询统一使用只读事务）。"""
from __future__ import annotations

from contextlib import contextmanager

import psycopg

from .config import settings


@contextmanager
def get_conn():
    """只读查询连接：在显式只读事务中执行，任何写操作都被数据库拒绝。"""
    conn = psycopg.connect(settings.database_url)
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION READ ONLY")
            conn.execute(f"SET statement_timeout = {settings.statement_timeout_ms}")
            yield conn
        # with 退出时正常 COMMIT（只读事务提交无副作用）
    finally:
        conn.close()


@contextmanager
def get_admin_conn():
    """建库/灌数等初始化使用（仅启动期与 /sample/reset-db 调用）。"""
    conn = psycopg.connect(settings.database_url, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()
