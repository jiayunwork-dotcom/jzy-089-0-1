-- 样例业务库：地区 / 客户 / 商品 / 订单 / 订单明细
DROP TABLE IF EXISTS order_items CASCADE;
DROP TABLE IF EXISTS orders CASCADE;
DROP TABLE IF EXISTS customers CASCADE;
DROP TABLE IF EXISTS products CASCADE;
DROP TABLE IF EXISTS regions CASCADE;

CREATE TABLE regions (
    region_id   INTEGER PRIMARY KEY,
    region_name TEXT NOT NULL,        -- 大区
    province    TEXT NOT NULL,        -- 省
    city        TEXT NOT NULL         -- 市
);

CREATE TABLE customers (
    customer_id  INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL,
    region_id    INTEGER REFERENCES regions(region_id),
    segment      TEXT NOT NULL,       -- 个人 / 企业
    credit_limit NUMERIC(12, 2) NOT NULL
);

CREATE TABLE products (
    product_id   INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL,       -- 类目
    unit_price   NUMERIC(12, 2) NOT NULL,
    unit_cost    NUMERIC(12, 2) NOT NULL
);

CREATE TABLE orders (
    order_id    INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date  DATE NOT NULL,
    channel     TEXT NOT NULL,        -- 线上 / 门店
    freight     NUMERIC(12, 2) NOT NULL
);

CREATE TABLE order_items (
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    line_no    INTEGER NOT NULL,
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity   INTEGER NOT NULL,
    discount   NUMERIC(5, 4) NOT NULL,  -- 0~1 的折扣率
    PRIMARY KEY (order_id, line_no)
);

CREATE INDEX idx_orders_customer ON orders(customer_id);
CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_items_order ON order_items(order_id);
CREATE INDEX idx_items_product ON order_items(product_id);
