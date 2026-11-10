# CaseLedger

CaseLedger 是一个面向 Shopify 店铺的财务管理系统，覆盖订单同步、费用管理、平台账户、报表分析和自动化任务调度。

## 技术栈

- 后端：Flask + SQLAlchemy + Flask-Migrate
- 异步任务：Celery + Redis
- 前端：Umi Max / Ant Design Pro + React + TypeScript
- 数据库：SQLite 默认，支持通过 `DATABASE_URL` 切换
- 第三方：Shopify API、Stripe、PayPal、汇率转换

## 主要功能

- Shopify 订单同步与订单明细管理
- 费用录入、分类、批量删除与账户扣费
- 平台账户管理
- 财务报表、收入趋势、费用分析、利润分析
- 手续费配置与 Shopify 配置管理
- Celery 定时同步与后台任务

## 项目结构

```text
CaseLedger/
├── app/
│   ├── api/              # 后端 API
│   ├── models/           # 数据模型
│   ├── services/         # 业务服务层
│   └── main/             # 页面/视图路由
├── frontend/             # 前端工程
├── run.py                # Flask 启动入口
├── init_db.py            # 数据库初始化
├── celery_app.py         # Celery 配置
├── start_celery_worker.py
├── start_celery_beat.py
├── start_celery_full_worker.py
├── quick_start.sh        # macOS 快速启动
├── manage_services.sh    # macOS LaunchAgents 管理
├── start_tunnel.sh       # 本地端口隧道
└── launchd/              # LaunchAgents plist
```

## 环境要求

- Python 3.8+
- Node.js 20+
- Redis
- Git

## 本地启动

### 1. 后端

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python init_db.py
python run.py
```

后端默认地址：`http://127.0.0.1:5000`

### 2. 前端

```bash
cd frontend
npm install
npm run start:dev
```

前端默认端口：`http://127.0.0.1:8000`

### 3. Celery

```bash
python start_celery_worker.py
python start_celery_beat.py
python start_celery_full_worker.py
```

## 一键启动

macOS 下可以使用：

```bash
./quick_start.sh
```

或通过 LaunchAgents 管理：

```bash
./manage_services.sh install
./manage_services.sh start
```

需要公网隧道时可运行：

```bash
./start_tunnel.sh
```

## 环境变量

## 代码规范与测试

### 分层约定

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 路由层 | `app/api/` | 只做参数解析与响应组装，不写 SQL、不写业务判断 |
| 服务层 | `app/services/` | 业务逻辑、事务边界、跨表操作 |
| 模型层 | `app/models/` | 仅表结构映射与轻量计算属性 |
| 公共层 | `app/utils/` | 鉴权、店铺范围、响应封装、异常定义 |

已完成下沉的模块（路由层无 SQL、无 `try/except`）：

| 模块 | 服务层 | 状态 |
| --- | --- | --- |
| orders | `app/services/order_service.py` | 完成（含 stats/recent 聚合下推） |
| expenses | `app/services/expense_service.py` | 完成（含账户余额扣减/回滚） |
| accounts | `app/services/account_service.py` | 完成（账户/充值/消耗/汇总，余额变更集中管理） |
| order_costs | `app/services/order_cost_service.py` | 完成（确认扣费原子化：先校验全部账户再扣减） |
| platform_accounts | `app/services/platform_account_service.py` | 完成（凭据加密存储，全接口仅管理员） |
| settings | `app/services/settings_service.py` | 完成（默认店铺凭据 + 费用配置；密钥打码回显不回写） |
| users | `app/services/user_service.py` | 完成（子账户 CRUD、店铺授权、改密） |
| sync | `app/services/sync_service.py` | 完成（按店铺解析范围 + 逐店同步与统计汇总） |
| auth | `app/services/auth_service.py` | 完成（凭据校验、管理员引导、登录限流状态机） |
| shops | `app/services/shop_service.py` | 完成（店铺 CRUD + 订单/费用计数） |
| reports | `app/services/report_service.py` | 完成（趋势/费用/利润/概览/多周期报表，全部一次取数） |

**业务逻辑下沉已 100% 完成**：`app/api/` 只做参数解析与响应组装，不含 SQL、事务与业务分支；
全部写操作集中在 `app/services/`。

账户余额的记账规则：**只有 completed 状态的充值才增加余额，pending 充值需确认后入账且只能确认一次**；
消耗在扣减的同时写入 consumption 流水，二者在同一事务内提交。`tests/test_accounts.py` 用
`余额 == 期初 + 已完成充值 - 全部消耗` 这个不变量做回归保护。

### 账户对账

```bash
python scripts/reconcile_accounts.py          # 只读对账报告
python scripts/reconcile_accounts.py --json   # 机器可读，便于接入监控
```

脚本逐个账户核对 `余额 == 期初 + 已完成充值 - 消耗`，并给出三档结论：
`OK`（完全对账）/ `UNCONFIRMED`（余额高于应有金额，需核对）/
`ANOMALY`（余额低于应有金额，说明流水缺失或余额被改动）。存在 ANOMALY 时退出码为 1，
可直接用于定时巡检。

```bash
python scripts/reconcile_accounts.py --detail 18   # 查某个账户的最近流水，定位差额
```

账户的 `opening_balance`（期初余额）字段用于让历史账户对账一致；老库需执行一次：

```bash
python migrate_add_account_opening_balance.py
```

该脚本会把「实际余额 −（已完成充值 − 消耗）」作为期初余额回填（非负时），
无法用期初余额解释的账户保持 0 并单独列出，避免把真实差额掩盖掉。

## 订单客户信息与商品明细

订单详情页的「客户信息 / 收货地址 / 商品明细」来自：

| 数据 | 存放位置 | 说明 |
| --- | --- | --- |
| 商品明细 | `order_items` 表 | 商品名、规格、SKU、数量、单价、折扣，一单多行 |
| 收货地址 | `orders.shipping_address`（JSON） | name/phone/address1/address2/city/province/country/zip |
| 客户联系方式 | `orders.customer_name` / `customer_email` / `customer_phone` | 见下方隐私限制 |

### ⚠️ Shopify 客户隐私限制（重要）

Shopify 对个人身份信息（PII）有额外限制，官方报错原文：

> This app is not approved to access the Customer object. Access to personally identifiable
> information (PII) like customer names, addresses, emails, phone numbers is only available
> on **Shopify, Advanced, and Plus plans**.

也就是说，**店铺套餐必须是 Shopify / Advanced / Plus**，并且应用要获批「客户数据访问权限」，
Admin API 才会返回姓名、邮箱、电话、门牌地址。Basic 套餐下这些字段一律返回 `null`，
省份/国家等非 PII 字段不受影响。

本店当前是 `Basic` 套餐，因此实际能拿到：

- ✅ 商品明细、金额、币种、收货国家/省份
- ❌ 客户姓名、邮箱、电话、街道地址与邮编

三种处理方式：

1. **升级套餐**到 Shopify 及以上，并在应用配置里申请客户数据权限
   （后台：设置 → 应用 → 开发应用 → 对应应用 → Configuration）。
2. **导入订单 CSV**：Shopify 后台「订单 → 导出」的 CSV 含客户姓名/邮箱/电话/收货地址，
   商家侧导出不受 API 限制。
3. 保持现状：详情页会提示「详细门牌被 Shopify 隐私策略屏蔽」，其余信息照常可用。

历史遗留：早期代码把客户信息写死成 `xxxx` / `xxxxxxx`，已清理为 `NULL`，避免展示假数据。

### 回填与同步

```bash
# 全量回填客户信息 + 收货地址 + 商品明细（走订单列表接口，几十次请求即可）
python scripts/backfill_order_details.py
```

新订单在 `sync_orders` 时自动写入商品明细（`_sync_order_items`）。

### 顺带修掉的同步 Bug

`shopify_order_id` / `shopify_product_id` / `transaction_id` 在库里是字符串列，而 Shopify
返回的是整数。SQLite 会隐式转换所以一直没暴露，**迁到 PostgreSQL 后订单同步会全部报错**
（`operator does not exist: character varying = bigint`），表现为「同步成功但一单没进来」。
已在同步代码里统一 `str(...)`，并让 `_process_payment` 出错时抛异常而不是吞掉，
避免事务进入 aborted 状态后连锁失败。

## 支付手续费与真实到账

手续费不再只靠费率表估算，而是尽量取网关的真实数字。两条链路来源不同：

| 支付方式 | 真实数据来源 | 说明 |
| --- | --- | --- |
| PayPal | 订单交易回执 `transactions[].receipt` | `seller_receivable_breakdown` 里有 `paypal_fee` / `net_amount` / `receivable_amount` / 换汇价 |
| Shopify Payments | 结算流水 `shopify_payments/balance/transactions` | 每条流水带 `fee` / `net`，用 `source_order_id` 对应订单 |

订单交易顶层那个 `fee` 字段一直是空的（网关不上报），真正的明细在回执里，这是本功能的关键。

### 币种口径（重要）

订单记的是店铺币种（如 USD），买家可能用 AUD / CAD / GBP 付款，而 Shopify Payments
的结算币种又可能是第三种（本项目实测为 HKD）。因此：

| 字段 | 含义 |
| --- | --- |
| `orders.currency` | 订单/店铺币种 |
| `orders.presentment_currency` | 买家实际付款币种 |
| `payments.currency` | 这笔支付的实际扣款币种 |
| `payments.amount` | 实际扣款金额（`payments.currency` 计） |
| `payments.amount_in_order_currency` | 上述金额折合订单币种 |
| `payments.real_fee` / `fee_currency` | 网关真实手续费及其币种 |
| `payments.total_fee` | 手续费折合订单币种（订单利润直接用这个） |
| `payments.real_net` / `settlement_amount` / `settlement_currency` | 真实净额与到账金额 |
| `payments.fee_source` | `gateway` = 真实值；`estimated` = 费率表估算 |

`orders.actual_received` 的计算规则：**有网关真实净额就用真实净额**，
没有才退回「订单金额 − 手续费估算」。所以列表里的「实际到账」在有真实数据时
就是钱真正到账的数字，和银行/PayPal 余额对得上。

### 数据来源标识

`payments.fee_source` 明确区分两种数据，订单详情页对每笔支付显示「真实 / 估算」标签：

- `gateway`：来自网关回执或结算流水。
- `estimated`：来自 `fee_configs` 费率表。PayPal 2025-11 之前的交易在 Shopify 侧
  只返回交易号、不返回费用明细，这部分只能保留估算。

### 同步与回填

```bash
./scripts/backup_db.sh                       # 改库前先备份
flask db upgrade                             # 应用迁移（新增 payments/orders 字段）

# 一次性回填历史数据
python scripts/backfill_gateway_fees.py --shopify-payments      # 结算流水（快）
python scripts/backfill_gateway_fees.py --paypal --only-missing  # PayPal 回执（慢，逐单请求）
python scripts/backfill_presentment_currency.py                 # 买家币种回填
```

日常增量同步走接口（管理员/子账户按店铺权限）：

```bash
curl -X POST http://127.0.0.1:5010/api/sync/gateway-fees \
  -H "Authorization: Bearer <token>" -H 'Content-Type: application/json' \
  -d '{"shop_id": 1, "paypal": false}'
```

`paypal=true` 会逐单拉交易回执，耗时与订单量成正比，建议放进定时任务低频执行；
结算流水一次翻页即可覆盖全量，可以频繁跑。

### 排障脚本（只读，不改数据）

| 脚本 | 用途 |
| --- | --- |
| `scripts/probe_shopify_fees.py` | 看交易回执/结算流水里到底有没有手续费字段 |
| `scripts/probe_paypal_fees.py` | 看 PayPal 交易是否有交易号、是否有费用明细 |
| `scripts/analyze_paypal_fees.py` | 按币种统计真实费率，并与系统估算逐笔对比 |

## 收入（额外收入）

费用模块同时承载「支出」和「额外收入」，通过流水的 `record_type` 区分：

| record_type | 含义 | 账户影响 | 报表影响 |
| --- | --- | --- | --- |
| `expense`（默认） | 支出：广告费、商品成本、物流费… | 关联账户则扣减余额并写消耗流水 | 计入费用 |
| `income` | 额外收入：平台补贴、退款返还、利息、赔偿… | 关联账户则**增加**余额并写一条已完成充值（`transaction_id=income:<id>`） | 计入收入 |

要点：

- 收入和支出共用 `/api/expenses`（列表可用 `record_type` 过滤）与同一套统计接口，前端在「费用」页统一查看。
- 收入类别与支出类别完全独立：`GET /api/expenses/income-categories`。
- **收入永远不会被计入费用统计**（月度费用分析、费用合计等都按 `record_type='expense'` 过滤）。
- 删除收入记录会精确回退账户余额并删除对应的充值流水，与删除支出的处理对称。
- 额外收入会进入收入趋势、利润分析、日报表和财务概览的「收入」侧，并在趋势中单独给出 `extra_income` 字段。

升级已有数据库：

```bash
./scripts/backup_db.sh
python migrate_add_income_records.py     # 幂等：加 record_type 列 + 索引，历史数据标记为支出
```

报表查询已消除 N+1（30 天区间从约 120 条 SQL 降到 5 条），并有查询次数回归测试守住
（`tests/test_reports.py::test_profit_analysis_does_not_scale_queries_with_days`）。

### 统一响应契约

```json
{"success": true,  "data": {...}, "message": "可选"}
{"success": false, "code": "NOT_FOUND", "message": "订单不存在"}
```

列表接口统一在 `data` 内返回业务数组 + `pagination` 分页块。
错误统一抛 `app/utils/errors.py` 中的异常（`NotFoundError` / `ForbiddenError` / `ValidationError` ...），
由全局 errorhandler 渲染，业务代码里不再手写 `except Exception`。

### 常用命令

```bash
make install-dev    # 安装开发依赖（pytest / ruff）
make test           # 后端 pytest + 前端 jest
make lint           # 后端 ruff + 前端 tsc/biome
make format         # 后端 ruff format
```

后端测试位于 `tests/`，使用独立的临时 SQLite 库，不会污染 `instance/caseLedger.db`：

```bash
pytest -q                    # 全部用例
pytest tests/test_auth.py    # 只跑鉴权用例
```

### 持续集成

`.github/workflows/ci.yml`：后端跑 ruff + pytest，前端跑 tsc + biome + jest + build，PR 必须全绿。

### 静态检查基线

- 后端 `ruff`（pyproject.toml）：当前 `E4/E7/E9/F/W` 全绿。
- 前端 `biome`（frontend/biome.json）：errors 为 0，仅保留少量 warning。
- 说明：`ruff format` 尚未全量执行（54 个文件存在格式化差异），可择机执行 `make format` 一次性对齐。

### 依赖卫生

- `requirements.txt` 只保留代码真正引用的包；已移除 `stripe` / `paypalrestsdk` / `pandas` / `openpyxl` / `APScheduler`
  （全项目零引用，其中 pandas 还会导致 CI 安装失败）。
- `cryptography` 是平台账号加密（Fernet）的直接依赖，必须显式声明，不能依赖传递依赖。
- 数据库驱动 `psycopg2-binary`（PostgreSQL）与 `PyMySQL`（MySQL）由 `DATABASE_URL` 决定实际使用者，两者都保留。

## 数据库与迁移

开发环境默认使用 SQLite；**生产环境建议使用 PostgreSQL**（多进程/多线程并发写、外键约束、备份恢复都更可靠）。
连接方式由 `.env` 的 `DATABASE_URL` 决定，代码层是 SQLAlchemy，切换数据库不需要改业务代码。

支持三种连接串写法（启动时会自动补全驱动）：

```env
DATABASE_URL=postgresql://caseledger:password@127.0.0.1:5432/caseledger
DATABASE_URL=mysql://user:password@127.0.0.1:3306/caseledger
DATABASE_URL=sqlite:////opt/caseledger/instance/caseLedger.db
```

### 表结构管理（Alembic）

```bash
flask db upgrade      # 建表 / 升级表结构（服务器首次部署执行，幂等）
flask db migrate -m "说明"   # 改了模型后生成新的迁移脚本
python init_db.py     # 只补默认数据（手续费配置），幂等
```

### 场景一：全新服务器部署

```bash
sudo -u postgres psql -c "CREATE USER caseledger WITH PASSWORD '强密码';"
sudo -u postgres psql -c "CREATE DATABASE caseledger OWNER caseledger ENCODING 'UTF8';"
# .env 中配置 DATABASE_URL 后：
flask db upgrade
python init_db.py
```

### 场景二：把现有 SQLite 数据迁到 PostgreSQL

```bash
# 1. 备份现有 SQLite
./scripts/backup_db.sh

# 2. 先看源库数据量和字段长度预检
python migrate_to_postgres.py --dry-run

# 3. 正式迁移（--recreate 会先重建目标表结构；目标库非空时需要）
python migrate_to_postgres.py --recreate

# 4. 标记迁移版本
flask db stamp head
```

### 本地开发连 PostgreSQL（macOS + Homebrew）

```bash
brew services start postgresql@14
psql -d postgres -c "CREATE ROLE caseledger LOGIN PASSWORD '你的密码';"
psql -d postgres -c "CREATE DATABASE caseledger OWNER caseledger ENCODING 'UTF8';"

# .env 指向新库
# DATABASE_URL=postgresql://caseledger:你的密码@127.0.0.1:5432/caseledger

flask db upgrade    # 建表
python migrate_to_postgres.py --source sqlite:////绝对路径/instance/caseLedger.db   # 迁数据
```

后端换端口调试时，前端代理可用环境变量覆盖：

```bash
cd frontend && PORT=8002 API_TARGET=http://127.0.0.1:5010 npm run start:dev
```

脚本会按外键顺序复制数据、逐表核对行数、核对订单/支付/费用金额合计，并重置 PostgreSQL 自增序列；
任何一项不一致都会明确报错并以非 0 退出码结束。

### 备份与恢复

```bash
./scripts/backup_db.sh 30          # 备份（PostgreSQL 用 pg_dump，SQLite 用 sqlite3 .backup），保留 30 天

# 恢复 PostgreSQL
pg_restore --clean --no-owner -d "postgresql://user:pw@host:5432/caseledger" backups/auto_caseledger_pg_YYYYmmdd_HHMMSS.dump

# 恢复 SQLite：直接替换文件即可（先停服务）
```

`deploy.sh` 已内置 `caseledger-backup.timer`，服务器上每天 03:00 自动备份。

## 多店铺与子账户

## 时间与时区约定

**存储统一 UTC，展示统一北京时间（UTC+8）。**

| 环节 | 规则 |
| --- | --- |
| 写入 | 所有时间戳用 `datetime.utcnow()`，**不要用 `datetime.now()`**（本地时间会让部署到 UTC 服务器后整体偏移） |
| 展示 | 前端用 `@/utils/timezone` 的 `formatBeijingTime` / `formatShortBeijingTime`（内部 `dayjs.utc(x).tz('Asia/Shanghai')`） |
| 查询 | 日期范围用 `convertDateRangeToUTC` 把北京时间区间转成 UTC 再传给后端 |
| 纯日期字段 | `expense_date` / `consumption_date` / `cost_date` / `recharge_date(日期部分)` 属于业务日期，按用户填写值原样存储与展示，不做时区换算 |

历史遗留问题与处理：费用/消耗记录早期用 `datetime.now()`（本地时间）写入，与订单等表的 UTC 口径不一致。
2026-09 已通过迁移 `b3f1c9a24d10` 把这些时间戳整体换算为 UTC，并在代码中统一改为 `datetime.utcnow()`，
因此现在展示层可以无差别地做「UTC → 北京时间」转换。

## UI 设计规范

整体视觉方向：**专业蓝灰白 + 浅层次阴影 + Inter 字体**，面向数据密集的财务后台。
所有设计令牌集中在一处，改主题不用翻组件代码：

| 位置 | 作用 |
| --- | --- |
| `frontend/config/config.ts` → `antd.configProvider.theme` | Ant Design 组件令牌（主色、圆角、控件高度、卡片/表格/菜单等组件级样式） |
| `frontend/config/defaultSettings.ts` → `token` | ProLayout 布局令牌（侧边栏、顶栏、页面容器配色与间距） |
| `frontend/src/global.less` | CSS 变量（`--cl-*`）与全局细节（滚动条、表头、卡片阴影、等宽数字） |

核心令牌：

```css
--cl-primary: #1e40af;   /* 主色：专业深蓝 */
--cl-accent:  #f59e0b;   /* 强调色：琥珀 */
--cl-bg:      #f5f7fb;   /* 页面底色 */
--cl-surface: #ffffff;   /* 卡片面 */
--cl-border:  #e9eef6;   /* 边框 */
--cl-text:    #0f172a;   /* 主文字 */
--cl-radius:  12px;      /* 卡片圆角 */
--cl-shadow-card: 0 1px 2px rgba(15,23,42,.04), 0 12px 32px -20px rgba(15,23,42,.24);
```

设计约定（改界面时请遵守）：

- 金额、统计数字统一使用**等宽数字**（`font-variant-numeric: tabular-nums`，已全局生效），表格里的金额列右对齐。
- 卡片用统一浅阴影与 12px 圆角，不要直接写死阴影/色值，优先用 `--cl-*` 变量或主题令牌。
- 状态色固定语义：成功/正常 `#16A34A`、警告/强调 `#F59E0B`、错误/亏损 `#DC2626`。
- 图表与 KPI 卡片遵循「大数值 + 灰色小标题 + 左侧色条」的既有样式，保持一致性。

系统支持多店铺财务管理，管理员可以新增店铺、新增子账户，并把一个或多个店铺分配给子账户。

| 角色 | 可见范围 | 可执行操作 |
| --- | --- | --- |
| 管理员（admin） | 全部店铺、全部功能 | 新增/编辑/停用/删除店铺、管理子账户、分配店铺与功能权限、所有业务数据读写、平台账号凭据 |
| 子账户（user） | 仅被分配的店铺 + 仅被授予的功能 | 在被授予的模块内查看/录入被分配店铺的数据；不可管理店铺与账号，不可访问平台账号凭据 |

- 数据隔离：订单、商品、支付、费用、订单成本都带 `shop_id`，子账户只能读写被分配店铺的数据。
- 店铺切换：页面右上角的店铺选择器可切换「全部店铺 / 指定店铺」，切换后全站按店铺过滤。
- 授权即时生效：调整子账户的店铺分配或停用账号后，无需等待旧令牌过期即刻生效。

### 功能权限（子账户能看到哪些模块）

子账户除了分配店铺，还要分配**功能权限**——例如「张三」只勾选「订单管理 + 费用管理」，
他登录后菜单里就只有这两项，其它模块的接口也会返回 403。

| 权限 key | 模块 | 可否授予子账户 |
| --- | --- | --- |
| `dashboard` | 仪表板 | ✅ |
| `orders` | 订单管理（含订单费用） | ✅ |
| `accounts` | 资金账户 | ✅ |
| `expenses` | 费用管理（含额外收入） | ✅ |
| `reports` | 财务报表 | ✅ |
| `platform_accounts` | 平台账号凭据 | ❌ 仅管理员 |
| `shops` | 店铺管理 | ❌ 仅管理员 |
| `sub_accounts` | 子账户管理 | ❌ 仅管理员 |
| `settings` | 系统设置 | ❌ 仅管理员 |

要点：

- **后端强制校验**（`app/utils/permissions.py` 是权限唯一来源 + `before_request` 拦截），
  前端隐藏菜单只是体验，直接调接口同样 403。
- 功能权限与店铺权限是**叠加**关系：既要有模块权限，也只能看到被分配的店铺。
- 分配/调整后**立即生效**（无需重新登录），因为每次请求都会重新读取账号权限。
- 新建子账户默认**没有任何功能权限**（不勾选就什么都看不到）；
  升级前已存在的子账户由迁移自动补齐全部可授予权限，不会突然失去访问。
- 接口：`GET /api/permissions`（清单）、`PUT /api/users/<id>/permissions`（设置）、
  创建/修改账号时也可直接传 `permissions: [...]`。
- 资金账户（充值/消耗）属于公司级共享资源，所有登录用户可见；平台账号凭据仅管理员可见。

### 从单店铺升级到多店铺

已有数据库执行一次迁移即可（脚本幂等，可重复执行）：

```bash
# 1. 先备份数据库
cp instance/caseLedger.db "backups/caseLedger_$(date +%Y%m%d_%H%M%S).db"

# 2. 执行迁移：建表 + 加 shop_id 字段 + 原店铺升级为默认店铺 + 历史数据回填 + 创建管理员
python migrate_add_multi_shop.py
```

迁移会把原有 `shopify_configs` 里的店铺升级成第一条店铺记录，并把历史订单/商品/支付/费用全部归属到该店铺。

复制 `.env.example` 为 `.env` 后配置：

```env
FLASK_ENV=development
FLASK_DEBUG=False
SECRET_KEY=your-secret-key
JWT_SECRET_KEY=your-jwt-secret-key
ENCRYPTION_KEY=your-fernet-key
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-strong-password

DATABASE_URL=sqlite:///case_ledger.db
REDIS_URL=redis://localhost:6379/0

SHOPIFY_API_KEY=your-shopify-api-key
SHOPIFY_API_SECRET=your-shopify-api-secret
SHOPIFY_ACCESS_TOKEN=your-shopify-access-token
SHOPIFY_SHOP_URL=your-shop.myshopify.com
SHOPIFY_WEBHOOK_SECRET=your-webhook-secret

PAYPAL_CLIENT_ID=your-paypal-client-id
PAYPAL_CLIENT_SECRET=your-paypal-client-secret
PAYPAL_MODE=sandbox

STRIPE_SECRET_KEY=your-stripe-secret-key
STRIPE_PUBLISHABLE_KEY=your-stripe-publishable-key

HOST=127.0.0.1
PORT=5000
```

## 初始化数据库

```bash
python init_db.py
```

脚本会创建表并初始化默认手续费配置。

## 常用命令

### 后端

```bash
python run.py
python init_db.py
python start_celery_worker.py
python start_celery_beat.py
python start_celery_full_worker.py
```

### 前端

```bash
cd frontend
npm run start:dev
npm run lint
npm test
npm run build
```

## API 概览

### 登录

- 登录成功后签发 JWT，有效期由 `JWT_EXPIRES_HOURS` 控制（当前 **168 小时 = 7 天**，在 `.env` 中配置）。
- 到期后接口返回 401，前端自动清除本地令牌并跳回登录页。
- 停用账号、调整店铺授权**即时生效**（每次请求都会重新读取账号信息），无需等令牌过期；
  但修改密码不会让已登录的其他设备立即掉线（无状态 JWT）。

- `POST /api/login/account`
- `POST /api/login/outLogin`
- `POST /api/login/captcha`

### 订单

- `GET /api/orders`
- `GET /api/orders/<id>`
- `PUT /api/orders/<id>`
- `GET /api/orders/recent`
- `GET /api/orders/stats`

### 费用

- `GET /api/expenses`
- `POST /api/expenses`
- `PUT /api/expenses/<id>`
- `DELETE /api/expenses/<id>`
- `POST /api/expenses/batch-delete`
- `GET /api/expenses/summary`
- `GET /api/expenses/categories`

### 报表

- `GET /api/reports/financial-summary`
- `GET /api/reports/financial`
- `GET /api/reports/revenue-trend`
- `GET /api/reports/expense-analysis`
- `GET /api/reports/profit-analysis`

### 账户

- `GET /api/accounts`
- `POST /api/accounts`
- `PUT /api/accounts/<id>`
- `DELETE /api/accounts/<id>`
- `GET /api/accounts/<id>/recharges`
- `POST /api/accounts/<id>/recharges`
- `GET /api/accounts/<id>/consumptions`

### 平台账户

- `GET /api/platform-accounts`
- `POST /api/platform-accounts`
- `PUT /api/platform-accounts/<id>`
- `DELETE /api/platform-accounts/<id>`
- `GET /api/platform-accounts/platforms`
- `GET /api/platform-accounts/<id>/password`

### 设置

- `GET /api/settings/shopify`
- `POST /api/settings/shopify`
- `POST /api/settings/shopify/test`
- `POST /api/settings/shopify/sync`
- `GET /api/settings/fees`
- `POST /api/settings/fees`
- `PUT /api/settings/fees/<id>`
- `DELETE /api/settings/fees/<id>`

### 汇率

- `POST /api/exchange/convert`
- `GET /api/exchange/rates`
- `GET /api/exchange/supported-currencies`

## 页面功能

- 仪表板查看经营概览
- 订单、费用、账户、平台账户的增删改查
- 报表筛选、查看和打印
- Shopify 配置保存、测试和同步

## 鉴权说明

API 默认需要 `Authorization: Bearer <token>`。

- 登录接口返回 JWT
- 兼容旧的简单 token：`123`
- 登录中间件位于 `app/__init__.py`

## 定时任务

Celery 配置在 `celery_app.py`：

- `sync-shopify-orders-full-noon`
- `sync-shopify-orders-full-midnight`
- `sync-shopify-orders-hourly`
- `sync-shopify-products`
- `test-shopify-connection`

## 部署

服务器部署和 macOS 常驻运行说明分别可参考：

- `DEPLOYMENT_GUIDE.md`
- `manage_services.sh`
- `launchd/`

## 许可证

未单独声明
