"""内核测试公共工具。"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.kernel import QuerySpec, translate
from app.sample_model import build_sample_model


def spec(d: dict) -> QuerySpec:
    return QuerySpec.from_dict(d)


def gen(d: dict, **kw):
    model = build_sample_model()
    return model, translate(model, spec(d), **kw)


TEST_DSN = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://bi_user:bi_pass@/bi_demo?host=/tmp/pgrun&port=55432",
)
