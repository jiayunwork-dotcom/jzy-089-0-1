"""随产品自带的样例模型：地区/客户/商品/订单/明细。

与 seed/schema.sql 的物理结构一一对应；该文件同时是
"几组预期查询 -> 期望 SQL" 核对材料（见 sample_queries）。
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

from app.kernel.schema import (
    AggFunc,
    CalculatedField,
    Cardinality,
    Column,
    DataType,
    Dimension,
    Hierarchy,
    Join,
    JoinKey,
    Measure,
    Model,
    Table,
)


def build_sample_model() -> Model:
    model = Model()

    model.tables["regions"] = Table(
        id="regions", name="地区", physical_name="regions",
        columns=[
            Column("region_id", DataType.INTEGER, primary_key=True),
            Column("region_name", DataType.TEXT),
            Column("province", DataType.TEXT),
            Column("city", DataType.TEXT),
        ],
    )
    model.tables["customers"] = Table(
        id="customers", name="客户", physical_name="customers",
        columns=[
            Column("customer_id", DataType.INTEGER, primary_key=True),
            Column("customer_name", DataType.TEXT),
            Column("region_id", DataType.INTEGER),
            Column("segment", DataType.TEXT),
            Column("credit_limit", DataType.NUMERIC),
        ],
    )
    model.tables["products"] = Table(
        id="products", name="商品", physical_name="products",
        columns=[
            Column("product_id", DataType.INTEGER, primary_key=True),
            Column("product_name", DataType.TEXT),
            Column("category", DataType.TEXT),
            Column("unit_price", DataType.NUMERIC),
            Column("unit_cost", DataType.NUMERIC),
        ],
    )
    model.tables["orders"] = Table(
        id="orders", name="订单", physical_name="orders",
        columns=[
            Column("order_id", DataType.INTEGER, primary_key=True),
            Column("customer_id", DataType.INTEGER),
            Column("order_date", DataType.DATE),
            Column("channel", DataType.TEXT),
            Column("freight", DataType.NUMERIC),
        ],
    )
    model.tables["order_items"] = Table(
        id="order_items", name="订单明细", physical_name="order_items",
        columns=[
            Column("order_id", DataType.INTEGER, primary_key=True),
            Column("line_no", DataType.INTEGER, primary_key=True),
            Column("product_id", DataType.INTEGER),
            Column("quantity", DataType.INTEGER),
            Column("discount", DataType.NUMERIC),
        ],
    )

    # 关联（左 = one 侧 / 父，右 = many 侧 / 子；M:N 两侧都是多）
    model.joins["customers_regions"] = Join(
        id="customers_regions", left_table="regions", right_table="customers",
        keys=[JoinKey("region_id", "region_id")],
        cardinality=Cardinality.ONE_TO_MANY,
    )
    model.joins["orders_customers"] = Join(
        id="orders_customers", left_table="customers", right_table="orders",
        keys=[JoinKey("customer_id", "customer_id")],
        cardinality=Cardinality.ONE_TO_MANY,
    )
    model.joins["items_orders"] = Join(
        id="items_orders", left_table="orders", right_table="order_items",
        keys=[JoinKey("order_id", "order_id")],
        cardinality=Cardinality.ONE_TO_MANY,
    )
    model.joins["items_products"] = Join(
        id="items_products", left_table="products", right_table="order_items",
        keys=[JoinKey("product_id", "product_id")],
        cardinality=Cardinality.ONE_TO_MANY,
    )

    # 维度 ---------------------------------------------------------------
    model.dimensions["region"] = Dimension(
        id="region", name="大区", table="regions", column="region_name",
        hierarchy_id="geo", data_type=DataType.TEXT,
    )
    model.dimensions["province"] = Dimension(
        id="province", name="省", table="regions", column="province",
        hierarchy_id="geo", data_type=DataType.TEXT,
    )
    model.dimensions["city"] = Dimension(
        id="city", name="市", table="regions", column="city",
        hierarchy_id="geo", data_type=DataType.TEXT,
    )
    model.hierarchies["geo"] = Hierarchy(
        id="geo", name="地区层级", levels=["region", "province", "city"]
    )

    model.dimensions["year"] = Dimension(
        id="year", name="年", table="orders",
        expression="TRUNC_DATE(order_date, 'year')",
        hierarchy_id="time", data_type=DataType.DATE,
    )
    model.dimensions["quarter"] = Dimension(
        id="quarter", name="季度", table="orders",
        expression="TRUNC_DATE(order_date, 'quarter')",
        hierarchy_id="time", data_type=DataType.DATE,
    )
    model.dimensions["month"] = Dimension(
        id="month", name="月", table="orders",
        expression="TRUNC_DATE(order_date, 'month')",
        hierarchy_id="time", data_type=DataType.DATE,
    )
    model.dimensions["day"] = Dimension(
        id="day", name="日", table="orders",
        expression="TRUNC_DATE(order_date, 'day')",
        hierarchy_id="time", data_type=DataType.DATE,
    )
    model.hierarchies["time"] = Hierarchy(
        id="time", name="时间层级", levels=["year", "quarter", "month", "day"]
    )

    model.dimensions["category"] = Dimension(
        id="category", name="商品类目", table="products", column="category",
        data_type=DataType.TEXT,
    )
    model.dimensions["product"] = Dimension(
        id="product", name="商品", table="products", column="product_name",
        data_type=DataType.TEXT,
    )
    model.dimensions["channel"] = Dimension(
        id="channel", name="渠道", table="orders", column="channel",
        data_type=DataType.TEXT,
    )
    model.dimensions["segment"] = Dimension(
        id="segment", name="客户类型", table="customers", column="segment",
        data_type=DataType.TEXT,
    )

    # 度量 ---------------------------------------------------------------
    # 行金额 = quantity * products.unit_price * (1 - discount)：
    # order_items 经 items_products 多对一关联到 products，行级计算字段可直接引用。
    model.calculated_fields["line_amount"] = CalculatedField(
        id="line_amount", name="行金额", table="order_items",
        expression="quantity * unit_price * (1 - discount)",
        data_type=DataType.NUMERIC,
    )
    model.measures["sales"] = Measure(
        id="sales", name="销量", table="order_items", column="quantity",
        agg=AggFunc.SUM,
    )
    model.measures["sales_amount"] = Measure(
        id="sales_amount", name="销售金额", table="order_items",
        column=None,
        expression="quantity * unit_price * (1 - discount)",
        agg=AggFunc.SUM,
    )
    model.calculated_fields["gross_profit"] = CalculatedField(
        id="gross_profit", name="毛利", table="order_items",
        expression="quantity * (unit_price - unit_cost) * (1 - discount)",
        data_type=DataType.NUMERIC,
    )
    model.measures["line_count"] = Measure(
        id="line_count", name="明细行数", table="order_items", column="order_id",
        agg=AggFunc.COUNT,
    )
    model.measures["order_count"] = Measure(
        id="order_count", name="订单数(去重)", table="order_items", column="order_id",
        agg=AggFunc.COUNT_DISTINCT,
    )
    model.measures["avg_discount"] = Measure(
        id="avg_discount", name="平均折扣", table="order_items", column="discount",
        agg=AggFunc.AVG,
    )
    model.measures["total_credit"] = Measure(
        id="total_credit", name="客户授信总额", table="customers", column="credit_limit",
        agg=AggFunc.SUM,
    )
    model.measures["big_order_count"] = Measure(
        id="big_order_count", name="大件订单行数", table="order_items",
        column="quantity", agg=AggFunc.COUNT,
        filter_expression="quantity >= 5",
    )
    model.measures["freight_total"] = Measure(
        id="freight_total", name="运费合计", table="orders", column="freight",
        agg=AggFunc.SUM,
    )

    # 其它计算字段 --------------------------------------------------------
    model.calculated_fields["is_vip"] = CalculatedField(
        id="is_vip", name="是否VIP客户", table="customers",
        expression='IF(credit_limit >= 30000, TRUE, FALSE)',
        data_type=DataType.BOOLEAN,
    )
    model.calculated_fields["price_band"] = CalculatedField(
        id="price_band", name="价格带", table="products",
        expression='IF(unit_price >= 500, "高价", IF(unit_price >= 150, "中价", "低价"))',
        data_type=DataType.TEXT,
    )

    model.validate()
    return model


def default_model_path() -> Path:
    return Path(os.getenv("MODEL_PATH", "/data/model.json"))


def ensure_sample_model(path: Path | None = None) -> Model:
    """磁盘没有模型文件时写入样例模型。"""
    path = path or default_model_path()
    model = build_sample_model()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json_dumps(model.to_dict()), encoding="utf-8")
    return model


def _json_dumps(obj) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 几组预期查询（供人核对、也供测试断言结构）
# ---------------------------------------------------------------------------

SAMPLE_QUERIES = [
    {
        "name": "单表：按渠道汇总运费（单表度量，不应出现任何 JOIN）",
        "spec": {
            "rows": ["channel"],
            "values": [{"measure_id": "freight_total"}],
        },
        "expect": {"joins": 0},
    },
    {
        "name": "跨表：按大区统计销售金额（fact -> dim 路径，外层去重挂回）",
        "spec": {
            "rows": ["region"],
            "values": [{"measure_id": "sales"}],
            "filters": [],
        },
        "expect": {"tables": {"regions", "customers", "orders", "order_items"}},
    },
    {
        "name": "钻取：大区 -> 省（粒度变细，过滤与度量不变）",
        "spec": {
            "rows": ["region"],
            "values": [{"measure_id": "sales_amount"}],
            "filters": [{"field_id": "year", "op": "between",
                         "value": ["2024-01-01", "2024-06-30"]}],
        },
        "drill": "region",
        "expect": {"tables": {"regions", "customers", "orders", "order_items", "products"}},
    },
    {
        "name": "筛选：只看线上渠道 + 枚举类目",
        "spec": {
            "rows": ["category"],
            "values": [{"measure_id": "sales_amount"}],
            "filters": [
                {"field_id": "channel", "op": "eq", "value": "线上"},
                {"field_id": "category", "op": "in",
                 "value": ["电子产品", "家居"]},
            ],
        },
        "expect": {"tables": {"products", "orders", "order_items"}},
    },
]


def sample_specs():
    """给测试使用的干净 spec 字典（去掉仅用于展示的元信息）。"""
    return copy.deepcopy(SAMPLE_QUERIES)
