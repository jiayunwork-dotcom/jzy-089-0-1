"""Bundled sample business model: regions / customers / products / orders /
order_lines / payments, with foreign-key relationships, drill hierarchies,
measures and sandboxed calculated fields.

This is the model the product ships with so the drag->SQL mapping can be
inspected immediately. See tests/expected_queries.py for fixed queries and
their expected SQL shape.
"""
from __future__ import annotations

from .modeling import (
    CalculatedField,
    Cardinality,
    Column,
    Dimension,
    Hierarchy,
    Join,
    Level,
    Measure,
    MeasureFilter,
    Model,
    Table,
)


def build_sample_model() -> Model:
    regions = Table(
        id="regions", db_name="regions", label="地区",
        columns=[
            Column(name="region_id", type="integer"),
            Column(name="region_name", type="string"),
            Column(name="area", type="string"),
        ],
    )
    customers = Table(
        id="customers", db_name="customers", label="客户",
        columns=[
            Column(name="customer_id", type="integer"),
            Column(name="customer_name", type="string"),
            Column(name="tier", type="string"),
            Column(name="city", type="string"),
            Column(name="region_id", type="integer"),
        ],
    )
    products = Table(
        id="products", db_name="products", label="商品",
        columns=[
            Column(name="product_id", type="integer"),
            Column(name="product_name", type="string"),
            Column(name="category", type="string"),
            Column(name="list_price", type="number"),
        ],
    )
    orders = Table(
        id="orders", db_name="orders", label="订单",
        columns=[
            Column(name="order_id", type="integer"),
            Column(name="order_no", type="string"),
            Column(name="customer_id", type="integer"),
            Column(name="order_date", type="date"),
            Column(name="status", type="string"),
            Column(name="total_amount", type="number"),
            Column(name="quantity_total", type="integer"),
        ],
    )
    order_lines = Table(
        id="order_lines", db_name="order_lines", label="订单明细",
        columns=[
            Column(name="line_id", type="integer"),
            Column(name="order_id", type="integer"),
            Column(name="product_id", type="integer"),
            Column(name="quantity", type="integer"),
            Column(name="unit_price", type="number"),
            Column(name="line_amount", type="number"),
        ],
    )
    payments = Table(
        id="payments", db_name="payments", label="支付",
        columns=[
            Column(name="payment_id", type="integer"),
            Column(name="order_id", type="integer"),
            Column(name="method", type="string"),
            Column(name="paid_at", type="datetime"),
            Column(name="amount", type="number"),
        ],
    )

    joins = [
        Join(
            id="customers_region",
            left_table="regions", left_column="region_id",
            right_table="customers", right_column="region_id",
            cardinality=Cardinality.one_to_many,
        ),
        Join(
            id="orders_customer",
            left_table="customers", left_column="customer_id",
            right_table="orders", right_column="customer_id",
            cardinality=Cardinality.one_to_many,
        ),
        Join(
            id="order_lines_order",
            left_table="orders", left_column="order_id",
            right_table="order_lines", right_column="order_id",
            cardinality=Cardinality.one_to_many,
        ),
        Join(
            id="order_lines_product",
            left_table="products", left_column="product_id",
            right_table="order_lines", right_column="product_id",
            cardinality=Cardinality.one_to_many,
        ),
        Join(
            id="payments_order",
            left_table="orders", left_column="order_id",
            right_table="payments", right_column="order_id",
            cardinality=Cardinality.one_to_many,
        ),
    ]

    geo = Hierarchy(
        id="geo", label="地区钻取",
        levels=[
            Level(id="area", label="大区", table="regions",
                  column="area", column_type="string"),
            Level(id="region_name", label="省/区", table="regions",
                  column="region_name", column_type="string"),
            Level(id="customer_city", label="城市",
                  table="customers", column="city",
                  column_type="string"),
        ],
    )
    date = Hierarchy(
        id="order_date_h", label="订单日期钻取",
        levels=[
            Level(id="order_year", label="年", table="orders",
                  column="order_date", column_type="integer",
                  expression="YEAR(order_date)"),
            Level(id="order_quarter", label="季度", table="orders",
                  column="order_date", column_type="integer",
                  expression="QUARTER(order_date)"),
            Level(id="order_month", label="月", table="orders",
                  column="order_date", column_type="integer",
                  expression="MONTH(order_date)"),
            Level(id="order_day", label="日", table="orders",
                  column="order_date", column_type="date"),
        ],
    )

    dimensions = [
        # geo levels (each level a real dimension used as the drill target)
        Dimension(id="area", label="大区", table="regions", column="area",
                  column_type="string", hierarchy_id="geo"),
        Dimension(id="region_name", label="省/区", table="regions",
                  column="region_name", column_type="string",
                  hierarchy_id="geo"),
        Dimension(id="customer_city", label="城市", table="customers",
                  column="city", column_type="string",
                  hierarchy_id="geo"),
        # plain dimensions
        Dimension(id="customer_tier", label="客户等级", table="customers",
                  column="tier", column_type="string"),
        Dimension(id="product_category", label="商品品类", table="products",
                  column="category", column_type="string"),
        Dimension(id="product_name", label="商品", table="products",
                  column="product_name", column_type="string"),
        Dimension(id="order_status", label="订单状态", table="orders",
                  column="status", column_type="string"),
        Dimension(id="payment_method", label="支付方式", table="payments",
                  column="method", column_type="string"),
        # date hierarchy levels
        Dimension(id="order_year", label="年", table="orders",
                  column="order_date", column_type="date",
                  hierarchy_id="order_date_h"),
        Dimension(id="order_quarter", label="季度", table="orders",
                  column="order_date", column_type="date",
                  hierarchy_id="order_date_h"),
        Dimension(id="order_month", label="月", table="orders",
                  column="order_date", column_type="date",
                  hierarchy_id="order_date_h"),
        Dimension(id="order_day", label="日", table="orders",
                  column="order_date", column_type="date",
                  hierarchy_id="order_date_h"),
    ]

    measures = [
        Measure(
            id="sales_amount", label="销售额", table="order_lines",
            column="line_amount", aggregation="sum",
        ),
        Measure(
            id="units_sold", label="销量", table="order_lines",
            column="quantity", aggregation="sum",
        ),
        Measure(
            id="orders_count", label="订单数", table="orders",
            column="order_id", aggregation="count_distinct",
        ),
        Measure(
            id="avg_order_total", label="平均客单额", table="orders",
            column="total_amount", aggregation="avg",
        ),
        Measure(
            id="max_order_total", label="最大订单额", table="orders",
            column="total_amount", aggregation="max",
        ),
        Measure(
            id="customers_count", label="客户数", table="customers",
            column="customer_id", aggregation="count_distinct",
        ),
        Measure(
            id="high_value_sales", label="大额订单额(>=5000)",
            table="orders", column="total_amount", aggregation="sum",
            filter=MeasureFilter(
                table="orders", column="total_amount", op="gte",
                values=[5000],
            ),
        ),
        Measure(
            id="completed_orders", label="已完成订单数", table="orders",
            column="order_id", aggregation="count_distinct",
            filter=MeasureFilter(
                table="orders", column="status", op="eq",
                values=["completed"],
            ),
        ),
        Measure(
            id="payment_amount", label="支付金额", table="payments",
            column="amount", aggregation="sum",
        ),
        Measure(
            id="payment_count", label="支付笔数", table="payments",
            column="payment_id", aggregation="count_distinct",
        ),
    ]

    calculated_fields = [
        CalculatedField(
            id="unit_margin", label="单笔毛利(单价-成本估计 80%)",
            table="order_lines",
            expression="unit_price * quantity * 0.2",
            result_type="number",
        ),
        CalculatedField(
            id="price_bucket", label="价格档",
            table="products",
            expression=(
                "IF(list_price >= 1000, 'high', "
                "IF(list_price >= 300, 'mid', 'low'))"
            ),
            result_type="string",
        ),
        CalculatedField(
            id="month_name", label="订单月份名",
            table="orders",
            expression="CONCAT('M', MONTH(order_date))",
            result_type="string",
        ),
    ]

    return Model(
        name="sample",
        label="样例电商业务库",
        tables=[regions, customers, products, orders, order_lines, payments],
        joins=joins,
        dimensions=dimensions,
        hierarchies=[geo, date],
        measures=measures,
        calculated_fields=calculated_fields,
    )
