-- Sample business database for DragQL.
-- Tables: regions 1 - N customers 1 - N orders 1 - N order_lines N - 1 products
--                              orders 1 - N payments   (second fan-out child)
CREATE TABLE IF NOT EXISTS regions (
    region_id   INTEGER PRIMARY KEY,
    region_name TEXT NOT NULL,
    area        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
    customer_id   INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL,
    tier          TEXT NOT NULL,
    city          TEXT NOT NULL,
    region_id     INTEGER NOT NULL REFERENCES regions(region_id)
);

CREATE TABLE IF NOT EXISTS products (
    product_id   INTEGER PRIMARY KEY,
    product_name TEXT NOT NULL,
    category     TEXT NOT NULL,
    list_price   NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    order_id       INTEGER PRIMARY KEY,
    order_no       TEXT NOT NULL UNIQUE,
    customer_id    INTEGER NOT NULL REFERENCES customers(customer_id),
    order_date     DATE NOT NULL,
    status         TEXT NOT NULL,
    total_amount   NUMERIC(12, 2) NOT NULL,
    quantity_total INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS order_lines (
    line_id     INTEGER PRIMARY KEY,
    order_id    INTEGER NOT NULL REFERENCES orders(order_id),
    product_id  INTEGER NOT NULL REFERENCES products(product_id),
    quantity    INTEGER NOT NULL,
    unit_price  NUMERIC(12, 2) NOT NULL,
    line_amount NUMERIC(12, 2) NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    payment_id INTEGER PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(order_id),
    method     TEXT NOT NULL,
    paid_at    TIMESTAMP,
    amount     NUMERIC(12, 2) NOT NULL
);

TRUNCATE payments, order_lines, orders, products, customers, regions CASCADE;

INSERT INTO regions (region_id, region_name, area) VALUES
    (1, '江苏', '华东'),
    (2, '浙江', '华东'),
    (3, '广东', '华南');

INSERT INTO customers (customer_id, customer_name, tier, city, region_id) VALUES
    (1, '苏宁云商', 'VIP',  '南京', 1),
    (2, '江南布衣', '普通', '苏州', 1),
    (3, '阿里巴巴', 'VIP',  '杭州', 2),
    (4, '网易严选', '普通', '杭州', 2),
    (5, '腾讯',     'VIP',  '深圳', 3),
    (6, '恒大',     '普通', '广州', 3);

INSERT INTO products (product_id, product_name, category, list_price) VALUES
    (1, '笔记本电脑',   '电子', 6000.00),
    (2, '机械键盘',     '电子',  400.00),
    (3, '人体工学椅',   '家居', 1500.00),
    (4, '保温杯',       '家居',  120.00),
    (5, '手机',         '电子', 4000.00);

-- order header totals match the sum of their lines below.
INSERT INTO orders (order_id, order_no, customer_id, order_date, status,
                    total_amount, quantity_total) VALUES
    (1, 'SO-1001', 1, DATE '2024-01-05', 'completed', 6120.00,  2),
    (2, 'SO-1002', 3, DATE '2024-02-11', 'completed', 4000.00,  1),
    (3, 'SO-1003', 5, DATE '2024-03-20', 'pending',   1500.00,  1),
    (4, 'SO-1004', 1, DATE '2024-04-02', 'completed', 2000.00, 12),
    (5, 'SO-1005', 6, DATE '2024-04-18', 'completed',  760.00,  4),
    (6, 'SO-1006', 4, DATE '2024-05-09', 'cancelled', 3400.00,  3),
    (7, 'SO-1007', 2, DATE '2024-06-15', 'completed',  520.00,  2),
    (8, 'SO-1008', 5, DATE '2024-07-21', 'completed', 4960.00,  9);

INSERT INTO order_lines (line_id, order_id, product_id, quantity, unit_price, line_amount) VALUES
    ( 1, 1, 1,  1, 6000.00, 6000.00),
    ( 2, 1, 4,  1,  120.00,  120.00),
    ( 3, 2, 5,  1, 4000.00, 4000.00),
    ( 4, 3, 3,  1, 1500.00, 1500.00),
    ( 5, 4, 2,  2,  400.00,  800.00),
    ( 6, 4, 4, 10,  120.00, 1200.00),
    ( 7, 5, 4,  3,  120.00,  360.00),
    ( 8, 5, 2,  1,  400.00,  400.00),
    ( 9, 6, 3,  2, 1500.00, 3000.00),
    (10, 6, 2,  1,  400.00,  400.00),
    (11, 7, 2,  1,  400.00,  400.00),
    (12, 7, 4,  1,  120.00,  120.00),
    (13, 8, 5,  1, 4000.00, 4000.00),
    (14, 8, 4,  8,  120.00,  960.00);

-- Orders 1 and 5 deliberately have TWO payments AND TWO lines: a naive
-- join cross-multiplies them (6120 -> 12240); the per-measure CTE design
-- keeps each total correct.
INSERT INTO payments (payment_id, order_id, method, paid_at, amount) VALUES
    (1, 1, 'card', TIMESTAMP '2024-01-05 10:00:00', 6000.00),
    (2, 1, 'card', TIMESTAMP '2024-01-05 10:05:00',  120.00),
    (3, 2, 'card', TIMESTAMP '2024-02-11 09:00:00', 4000.00),
    (4, 4, 'cash', TIMESTAMP '2024-04-02 14:00:00', 2000.00),
    (5, 5, 'cash', TIMESTAMP '2024-04-18 11:00:00',  360.00),
    (6, 5, 'cash', TIMESTAMP '2024-04-18 11:10:00',  400.00),
    (7, 7, 'card', TIMESTAMP '2024-06-15 16:30:00',  520.00),
    (8, 8, 'card', TIMESTAMP '2024-07-21 18:00:00', 4960.00);
