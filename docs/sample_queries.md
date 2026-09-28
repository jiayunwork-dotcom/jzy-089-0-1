# 样例模型：拖拽意图 -> 翻译 SQL 对照

> 展示几组典型拖拽在 SQL 内核中的稳定翻译结果。
> 所有字面量均为 %s 绑定参数；跨表度量经 DISTINCT 主键去重子查询聚合。

## 单表：按渠道汇总运费（单表度量，不应出现任何 JOIN）

拖拽意图：
```json
{
  "rows": [
    "channel"
  ],
  "values": [
    {
      "measure_id": "freight_total"
    }
  ]
}
```
生成 SQL：
```sql
SELECT "t0"."channel" AS "g1", SUM("t0"."freight") AS "m1"
FROM "orders" AS "t0"
GROUP BY 1
LIMIT %s::bigint
```

## 跨表：按大区统计销售金额（fact -> dim 路径，外层去重挂回）

拖拽意图：
```json
{
  "rows": [
    "region"
  ],
  "values": [
    {
      "measure_id": "sales"
    }
  ],
  "filters": []
}
```
生成 SQL：
```sql
SELECT "base"."g1" AS "g1", "base"."m1" AS "m1"
FROM (
  SELECT "t3"."region_name" AS "g1", SUM("t0"."quantity") AS "m1"
  FROM "order_items" AS "t0"
  LEFT JOIN "orders" AS "t1"
    ON "t0"."order_id" IS NOT DISTINCT FROM "t1"."order_id"
  LEFT JOIN "customers" AS "t2"
    ON "t1"."customer_id" IS NOT DISTINCT FROM "t2"."customer_id"
  LEFT JOIN "regions" AS "t3"
    ON "t2"."region_id" IS NOT DISTINCT FROM "t3"."region_id"
  GROUP BY 1
) AS "base"
LIMIT %s::bigint
```

## 钻取：大区 -> 省（粒度变细，过滤与度量不变）

拖拽意图：
```json
{
  "rows": [
    "region"
  ],
  "values": [
    {
      "measure_id": "sales_amount"
    }
  ],
  "filters": [
    {
      "field_id": "year",
      "op": "between",
      "value": [
        "2024-01-01",
        "2024-06-30"
      ]
    }
  ]
}
```
生成 SQL：
```sql
SELECT "base"."g1" AS "g1", "base"."g2" AS "g2", "base"."m1" AS "m1"
FROM (
  SELECT "t4"."region_name" AS "g1", "t4"."province" AS "g2", SUM((("t0"."quantity" * "t2"."unit_price") * (%s::numeric - "t0"."discount"))) AS "m1"
  FROM "order_items" AS "t0"
  LEFT JOIN "orders" AS "t1"
    ON "t0"."order_id" IS NOT DISTINCT FROM "t1"."order_id"
  LEFT JOIN "products" AS "t2"
    ON "t0"."product_id" IS NOT DISTINCT FROM "t2"."product_id"
  LEFT JOIN "customers" AS "t3"
    ON "t1"."customer_id" IS NOT DISTINCT FROM "t3"."customer_id"
  LEFT JOIN "regions" AS "t4"
    ON "t3"."region_id" IS NOT DISTINCT FROM "t4"."region_id"
  WHERE (date_trunc(%s::text, "t1"."order_date")::date BETWEEN %s::date AND %s::date)
  GROUP BY 1, 2
) AS "base"
LIMIT %s::bigint
```

## 筛选：只看线上渠道 + 枚举类目

拖拽意图：
```json
{
  "rows": [
    "category"
  ],
  "values": [
    {
      "measure_id": "sales_amount"
    }
  ],
  "filters": [
    {
      "field_id": "channel",
      "op": "eq",
      "value": "线上"
    },
    {
      "field_id": "category",
      "op": "in",
      "value": [
        "电子产品",
        "家居"
      ]
    }
  ]
}
```
生成 SQL：
```sql
SELECT "base"."g1" AS "g1", "base"."m1" AS "m1"
FROM (
  SELECT "t2"."category" AS "g1", SUM((("t0"."quantity" * "t2"."unit_price") * (%s::numeric - "t0"."discount"))) AS "m1"
  FROM "order_items" AS "t0"
  LEFT JOIN "orders" AS "t1"
    ON "t0"."order_id" IS NOT DISTINCT FROM "t1"."order_id"
  LEFT JOIN "products" AS "t2"
    ON "t0"."product_id" IS NOT DISTINCT FROM "t2"."product_id"
  WHERE ("t1"."channel" = %s::text) AND ("t2"."category" = ANY(%s::text[]))
  GROUP BY 1
) AS "base"
LIMIT %s::bigint
```
