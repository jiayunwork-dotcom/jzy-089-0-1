"""确定性样例数据生成（保证样例查询结果可复现）。

通过 DB_SERVICE_PING 等环境变量不需要任何外部输入即可复现：
地区 8 行 / 客户 30 / 商品 20 / 订单 200 / 明细 ~600，全部由固定伪随机序列生成。
"""
from __future__ import annotations

import os

import random

REGIONS = [
    # id, 大区, 省, 市
    (1, "华东大区", "浙江省", "杭州市"),
    (2, "华东大区", "浙江省", "宁波市"),
    (3, "华东大区", "江苏省", "南京市"),
    (4, "华南大区", "广东省", "广州市"),
    (5, "华南大区", "广东省", "深圳市"),
    (6, "华南大区", "福建省", "厦门市"),
    (7, "华北大区", "北京市", "北京市"),
    (8, "华北大区", "山东省", "济南市"),
]

SEGMENTS = ["个人", "企业"]
CHANNELS = ["线上", "门店"]
CATEGORIES = ["电子产品", "家居", "食品饮料"]

CATEGORY_PRODUCTS = {
    "电子产品": ["无线耳机", "机械键盘", "智能手表", "平板", "显示器", "路由器", "移动电源"],
    "家居": ["办公椅", "台灯", "保温杯", "收纳箱", "床垫", "香薰机"],
    "食品饮料": ["咖啡豆", "坚果礼盒", "气泡水", "燕麦片", "牛肉干", "蜂蜜", "酸奶"],
}


class _Rng:
    """固定种子的随机源（Mersenne Twister，跨平台统计性质足够好且可复现）。"""

    def __init__(self, seed: int = 20260928) -> None:
        self._r = random.Random(seed)

    def below(self, n: int) -> int:
        return self._r.randrange(n)


def generate() -> dict[str, list[tuple]]:
    rng = _Rng()

    customers: list[tuple] = []
    for i in range(1, 31):
        customers.append((
            i,
            f"客户{i:02d}",
            rng.below(len(REGIONS)) + 1,
            SEGMENTS[rng.below(2)],
            1000 * (rng.below(50) + 5),
        ))

    products: list[tuple] = []
    pid = 1
    for category, names in CATEGORY_PRODUCTS.items():
        for name in names:
            price = 50 + rng.below(950)
            cost = max(10, int(price * (0.4 + rng.below(30) / 100)))
            products.append((pid, f"{name}-{pid:02d}", category, price, cost))
            pid += 1

    orders: list[tuple] = []
    items: list[tuple] = []
    import datetime as _dt

    base = _dt.date(2024, 1, 1)
    for oid in range(1, 201):
        day = base + _dt.timedelta(days=rng.below(366))  # 2024 全年
        orders.append((
            oid,
            rng.below(30) + 1,
            day,
            CHANNELS[rng.below(2)],
            10 + rng.below(90),
        ))
        line_count = 1 + rng.below(4)  # 1~4 行明细
        used_products: set[int] = set()
        for line_no in range(1, line_count + 1):
            prod = rng.below(len(products)) + 1
            while prod in used_products:
                prod = rng.below(len(products)) + 1
            used_products.add(prod)
            items.append((
                oid,
                line_no,
                prod,
                1 + rng.below(9),
                round((rng.below(40) / 100), 4),  # 0~0.39 折扣
            ))

    return {
        "regions": REGIONS,
        "customers": customers,
        "products": products,
        "orders": orders,
        "order_items": items,
    }


INSERT_SQL = {
    "regions": "INSERT INTO regions VALUES (%s, %s, %s, %s)",
    "customers": "INSERT INTO customers VALUES (%s, %s, %s, %s, %s)",
    "products": "INSERT INTO products VALUES (%s, %s, %s, %s, %s)",
    "orders": "INSERT INTO orders VALUES (%s, %s, %s, %s, %s)",
    "order_items": "INSERT INTO order_items VALUES (%s, %s, %s, %s, %s)",
}


def seed(conn) -> None:
    data = generate()
    with conn.cursor() as cur:
        for table in ("regions", "customers", "products", "orders", "order_items"):
            cur.executemany(INSERT_SQL[table], data[table])
    conn.commit()


def is_seeded(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('public.orders') IS NOT NULL")
        exists = cur.fetchone()[0]
        if not exists:
            return False
        cur.execute("SELECT COUNT(*) FROM orders")
        return cur.fetchone()[0] > 0


if __name__ == "__main__":  # pragma: no cover
    import psycopg

    dsn = os.getenv("DATABASE_URL", "postgresql://bi_user:bi_pass@localhost:5432/bi_demo")
    with psycopg.connect(dsn, autocommit=True) as conn:
        with open(os.path.join(os.path.dirname(__file__), "schema.sql"), "r", encoding="utf-8") as f:
            conn.execute(f.read())
    with psycopg.connect(dsn) as conn:
        seed(conn)
    print("seed done")
