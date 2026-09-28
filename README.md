# 拖拽式自助数据查询工具

让不懂 SQL 的分析师在浏览器里把想看的数据**拖出来**：左侧字段 → 行/列/数值/筛选，
后端把拖拽意图翻译成**正确、参数化、只读**的 PostgreSQL 查询并返回可排序的结果表。

核心只做一件事：**拖拽即生成查询**。不是报表平台，没有图表库。

---

## 一、快速开始（容器编排，一条命令）

```bash
docker compose up -d --build
# 前端页面： http://localhost:8080
# API：      http://localhost:8080/api/health
# PostgreSQL（可选直连核对）：localhost:55432  bi_user/bi_pass/bi_demo
```

三个服务：

| 服务 | 技术 | 说明 |
| --- | --- | --- |
| `db` | `postgres:16-alpine` | 样例业务库，首次启动自动建表灌数 |
| `backend` | Python 3.12 + FastAPI + psycopg3 | 建模 / 沙箱 / SQL 内核 / 只读执行 |
| `frontend` | React 18 + Vite，Nginx 托管静态产物 | 拖拽查询面板，反代 `/api` 到后端 |

首次启动时后端自动：
1. 若模型文件为空 → 落盘**样例模型**（地区/客户/商品/订单/订单明细，含层级、度量、计算字段）；
2. 若库中无 `orders` 表 → 执行 `seed/schema.sql` 并灌入**确定性**样例数据（结果可复现）。

重建/重置：

```bash
docker compose exec backend python -m seed.seed_data   # 重新灌数（需先建表）
# 界面"数据建模"页也有"重置为样例模型"按钮；开发时可用 POST /api/sample/reset-db 重建库
```

### 本地开发（不用容器）

```bash
# 后端
cd backend
pip install -r requirements.txt
export DATABASE_URL=postgresql://bi_user:bi_pass@localhost:5432/bi_demo
python -m app.main                       # :8000

# 前端（热更新，自动代理 /api -> :8000）
cd frontend
npm install && npm run dev               # :5173
```

---

## 二、功能与交互

- **左侧字段栏**：维度 / 度量 / 计算字段，原生 HTML5 拖拽。
- **四个投放区**：行（分组，自上而下）、列（附加分组）、数值（度量/计算字段聚合）、筛选。
- **实时查询**：投放/移除后 300ms 防抖自动请求，也可手动"运行/刷新"。
- **筛选控件**：文本下拉枚举（多选 `IN`）/ 日期区间 / 数值区间 / 比较 / 模糊匹配 / 空值。
- **排序结果表**：点表头切换 升序 → 降序 → 取消；主呈现就是表格。
- **层级钻取**：带下划线的维度值可点击，按声明的层级向下钻一层并自动重查
  （年→季→月→日、大区→省→市）。
- **生成 SQL 面板**：折叠展示最终 SQL，便于核对翻译结果。
- **建模页**：浏览表/关联/维度/层级/度量；计算字段表达式在"校验"时即报错并指出字符位置。

---

## 三、模块结构（SQL 生成与建模、路由严格分离）

```
backend/
├── app/
│   ├── main.py               # FastAPI 装配、静态托管、启动引导
│   ├── config.py             # 环境配置
│   ├── db.py                 # psycopg 连接（只读事务 / 管理员连接）
│   ├── api_schemas.py        # HTTP pydantic 模型
│   ├── model_store.py        # 模型 JSON 原子持久化
│   ├── service.py            # 业务编排（表达式校验、翻译/执行、枚举）
│   ├── query_executor.py     # 只读守卫 + 参数绑定执行 + 结果整形
│   ├── sample_model.py       # 样例模型 + 样例查询
│   ├── routers/query.py      # HTTP 路由（只做参数转换与错误映射）
│   └── kernel/               # ★ SQL 翻译内核（与 Web 框架完全解耦，可独立单测）
│       ├── schema.py         #   表/列/关联(1:1/1:N/M:N)/维度/层级/度量/计算字段
│       ├── query.py          #   拖拽意图 QuerySpec（行/列/数值/筛选/排序/limit）
│       ├── planner.py        #   连通性、根表选择、最小放大生成树、度量执行方案
│       ├── renderer.py       #   JOIN/GROUP BY/聚合/参数化 SQL 渲染
│       └── sandbox/          #   白名单表达式沙箱
│           ├── parser.py     #     词法 + 递归下降
│           ├── functions.py  #     函数白名单 / 日期单位白名单
│           ├── compiler.py   #     类型检查 + 编译为参数化 PG 表达式
│           └── errors.py     #     带字符偏移的错误
├── seed/
│   ├── schema.sql            # 样例业务库 DDL
│   └── seed_data.py          # 确定性数据生成与灌库
└── tests/                    # 自动化测试（unittest，无需外部框架）
frontend/
├── src/
│   ├── api.js                # 后端接口封装
│   └── components/
│       ├── QueryBuilder.jsx  # 拖拽查询面板主体
│       ├── FieldPalette.jsx  # 左侧字段
│       ├── DropZone.jsx      # 投放区
│       ├── FilterEditor.jsx  # 枚举/日期/数值筛选
│       ├── ResultTable.jsx   # 排序 + 钻取结果表
│       └── Modeler.jsx       # 建模浏览 + 表达式校验
└── Dockerfile / nginx.conf   # 构建后由 Nginx 托管
docs/sample_queries.md        # 几组拖拽 -> 期望 SQL 的对照材料
```

---

## 四、SQL 内核是怎么保证"翻译正确"的

给定"哪些维度分组、哪些度量聚合、哪些表、哪些筛选"：

1. **解析引用**：维度/度量/计算字段归属到表；沙箱表达式中的字段闭包也收集为涉及表。
2. **连通性检查**：被引用的表必须能通过已声明关联连通，否则拒绝（不猜连接）。
3. **选根表**：优先承载度量最多的表（事实表优先），再按放大边数、跳数、id 决出稳定结果。
4. **最小放大生成树**：Dijkstra，边权以"会放大行数的边"（1:N 的 one→many、M:N）为重，
   路径覆盖全部涉及表且**每张表只接入一次**。
5. **度量执行方式**：
   - 主树无放大边且度量在根表 → 直接聚合；
   - 其它一切情况（跨表度量、或存在放大边，包括根表度量）→ 先在子查询中
     **`SELECT DISTINCT 实体主键, 分组键, 原始值`** 去重，再按分组键
     `LEFT JOIN` 回主结果做二次聚合 —— JOIN 扇出无法复制事实行。
6. **筛选**：无放大路径的维度条件下推 `WHERE`；放大边之后的条件用相关
   `EXISTS` 半连接保持事实粒度；度量筛选进 `HAVING`；钻取只追加更细分组层级。
7. **参数化与只读**：所有用户字面量（含日期单位、LIMIT）经命名占位符编译，
   最终按出现顺序展开为 psycopg 的 `%s` 位置参数；执行前有 SQL 文本守卫，
   连接层再用 `SET TRANSACTION READ ONLY` 做数据库侧兜底。

---

## 五、自动化测试（规则即测试）

```bash
cd backend
# 纯内核测试无需数据库；有 PostgreSQL 时自动运行结果级集成测试
TEST_DATABASE_URL=postgresql://bi_user:bi_pass@localhost:5432/bi_demo \
  python -m unittest discover -s tests -v
```

当前 54 个用例，每个文件对应一条产品规则：

| 文件 | 守护的规则 / 容差 |
| --- | --- |
| `test_single_table.py` | 单表查询**不出现任何 JOIN**（也无多余包装子查询） |
| `test_join_path.py` | JOIN 路径覆盖所有被引用表（含中转表）；同一张表不重复连接；孤岛表被拒 |
| `test_fanout.py` | 1:N 放大路径下"多"侧求和不被复制；静态断言必须出现 `DISTINCT 主键` 去重子查询；集成断言结果等于独立基准（容差 2 位小数），并**显式证明朴素 JOIN 的数字更大** |
| `test_stability.py` | 同拖拽、同字段顺序 → 逐字符一致的 SQL 与参数；字段顺序变化被尊重 |
| `test_drilldown.py` | 钻取只追加更细分组；过滤与度量保持不变（SQL 片段与参数逐项对比） |
| `test_parameterization.py` | 标量/枚举/日期/数值/LIKE/LIMIT/表达式字面量全部绑定参数，SQL 文本不含用户值，LIKE 通配符被转义 |
| `test_readonly.py` | 只允许 SELECT/WITH；INSERT/UPDATE/DELETE/DDL/多语句/写关键字被拒；只读事务里写操作被数据库拒绝 |
| `test_sandbox.py` | 白名单函数、语法错误位置、未知字段、类型错误、危险函数名、循环引用 |
| `test_end_to_end.py` | HTTP 意图 → 真实 PG 执行：总计守恒、钻取前后总和一致、筛选/排序/限制生效 |

---

## 六、表达式沙箱（计算字段）

类电子表格表达式，保存时校验并返回 `{start, end}` 字符偏移：

- 运算：`+ - * / %`、比较、`AND OR NOT`、括号、`IF / IFNULL / COALESCE / IS NULL`
- 数学：`ABS ROUND FLOOR CEIL POWER MOD SQRT`
- 文本：`CONCAT UPPER LOWER TRIM SUBSTR LEFT RIGHT REPLACE LENGTH`
- 日期：`TRUNC_DATE(d,'year'|'quarter'|'month'|'week'|'day')`、
  `DATE_PART('year', d)`、`DATE_ADD(d, 'day', 7)`（单位白名单，禁止任意字符串）
- 没有任何系统/文件/网络函数；未知函数名、参数个数不符、类型不符都会被拒绝。
