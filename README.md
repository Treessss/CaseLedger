# CaseLedger

CaseLedger 是一个面向跨境电商（Shopify + 4PX）的一体化仓储、物流、售后、采购与财务管理系统。
它把订单、仓库作业、4PX 预申报、物流轨迹、售后补发、采购入库、库存、客服邮件和利润核算放在同一套数据里，
避免在多个 Excel / 平台后台之间反复对数。

## 功能总览

### 订单中心
- Shopify 订单同步（全量 / 增量 / Webhook）、订单详情、商品明细、支付与到账；
- 客户信息、收货地址、物流单号、履约状态；
- 订单级物流费（4PX 真实费用）与利润展示。

### 仓储作业
- **商品管理**：商品/颜色的中文名映射、按商品查看 SKU；
- **采购管理**：采购候选、采购清单、按快照确认发起采购、分批入库、货损/缺货；
- **库存管理**：按商品 → 颜色 → 型号分组，同颜色不同配置（+Wrist Chain / +Stand）合并展示；
- **4PX 预申报**：按目的地/件数/邮编自动建议物流产品（含加拿大智选不可达邮编回退），异步批量真实建单；
- **装箱核对**：按订单核对商品与装箱数量；
- **扫码出库**：扫实体面单条码出库、扣库存；
- **发货中心**：Shopify 已发货 ≠ 仓库已出库的对账、售后补发 4PX 对单、批量出库；
- **物流轨迹**：4PX 轨迹同步、未出库有轨迹自动补出库、异常/超时提醒、合并发货支持；
- **售后补发**：售后单、补发商品、费用、面单条码、CF 单回写 Shopify。

### 财务中心
- 费用管理（每日物流账单、广告费、商品成本等）；
- 资金账户、充值、消耗流水；
- 订单费用（物流费 / 方果费用 / 其他费用）与账户扣费；
- 财务报表、收入趋势、利润分析；
- **4PX 真实物流费自动归集**：按订单日期写入订单物流费，并自动生成每日物流费账单。

### 客服邮件
- Zoho 邮箱连接、邮件同步、会话聚合、附件（图片/视频）；
- AI 生成回复草稿、人工确认发送、忽略、批量处理；
- 按客户邮箱/订单号查询关联订单与物流；
- 邮件发送审计与发送限额。

### AI API
- 管理员创建 API Key（`cai_...`），按店铺、scope、IP 白名单、每日发送上限授权；
- 客服邮件、订单、物流、采购、库存、售后、主动发信等接口；
- OpenAPI：`GET /api/ai/openapi.json`；
- 文档：[生产接口文档](docs/AI-API-生产接口文档.md)、[给 AI 的提示词](docs/AI-API-提示词.md)。

### 系统管理
- 多店铺隔离、店铺 4PX / Shopify 凭据；
- 平台账号（密码加密存储）、员工账号与模块权限；
- AI API Key 管理与调用审计；
- 费用配置、汇率配置。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 后端 | Python 3.10+、Flask 2.3、SQLAlchemy、Flask-Migrate（Alembic） |
| 异步任务 | Celery + Redis（多队列：sync / mail / tracking / tracking_history / declaration） |
| 数据库 | PostgreSQL 14+（生产）；SQLite（本地零配置） |
| 前端 | Umi Max + Ant Design Pro + React + TypeScript |
| 部署 | Docker Compose（web / worker / beat / mail-worker / tracking-worker / declaration-worker） |
| 外部服务 | Shopify Admin API、4PX OpenAPI、Zoho Mail、PayPal、Stripe、汇率接口 |

## 目录结构

```text
CaseLedger/
├── app/
│   ├── api/                 # HTTP 路由（薄层：参数解析 + 响应组装）
│   ├── models/              # SQLAlchemy 模型
│   ├── services/            # 业务逻辑、事务边界、外部接口封装
│   ├── tasks.py             # Celery 任务
│   ├── webhooks/            # Shopify Webhook
│   ├── utils/               # 鉴权、店铺范围、响应、异常、时区
│   └── data/                # 邮编、地址等静态数据
├── frontend/                # Umi Max 前端
│   ├── src/pages/           # 页面（订单/仓储/财务/客服/系统管理）
│   ├── src/services/        # API 客户端
│   └── config/              # 路由、代理、主题
├── migrations/              # Alembic 迁移
├── tests/                   # pytest 后端测试
├── docs/                    # 业务规则、部署与 API 文档
├── deployment/isolated/     # Docker Compose / Dockerfile / Nginx / 备份脚本
├── scripts/                 # 运维脚本（对账、回填、迁移等）
├── celery_app.py            # Celery 配置、队列路由、定时任务
├── config.py                # 环境配置
├── run.py                   # Flask 入口
└── init_db.py               # 初始化默认数据
```

## 本地启动

### 环境要求

- Python 3.10+
- Node.js 20+
- PostgreSQL 14+（可选，本地可先用 SQLite）
- Redis（Celery 必需）

### 1. 后端

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env
# 编辑 .env：至少填 SECRET_KEY、JWT_SECRET_KEY、ENCRYPTION_KEY、
# 管理员账号、DATABASE_URL、REDIS_URL、Shopify / 4PX / Zoho 凭据

flask db upgrade                  # 建表 / 升级表结构
python init_db.py                 # 初始化默认手续费配置（幂等）
python run.py                     # 默认 http://127.0.0.1:5000
```

如果只是本地快速体验，不接 PostgreSQL，可把 `DATABASE_URL` 留空使用 SQLite。

### 2. 前端

```bash
cd frontend
npm install
npm run start:dev                 # 默认 http://127.0.0.1:8000
# 后端不在 5000 时：
# API_TARGET=http://127.0.0.1:5010 npm run start:dev
```

### 3. Celery

本地至少启动一个 worker 和 beat：

```bash
python start_celery_worker.py
python start_celery_beat.py
```

需要处理邮件、物流轨迹、4PX 预申报时，再按队列启动对应 worker：

```bash
celery -A celery_app.celery worker -l info -Q mail -n mail@%h
celery -A celery_app.celery worker -l info -Q tracking -n tracking@%h
celery -A celery_app.celery worker -l info -Q tracking_history -n tracking-history@%h
celery -A celery_app.celery worker -l info -Q declaration -n declaration@%h
```

## Docker 部署

生产推荐直接使用 `deployment/isolated/compose.yaml`：

```bash
cd deployment/isolated
# 先把 env_file 路径改成你的实际配置目录（默认是 /opt/caseledger-copy/config/app.env）
docker compose -f compose.yaml build
docker compose -f compose.yaml up -d
```

服务组成：

| 服务 | 作用 |
| --- | --- |
| `web` | Flask / Gunicorn |
| `db` / `redis` | PostgreSQL / Redis |
| `worker` | 通用队列（celery / sync / sync_full / test） |
| `declaration-worker` | 4PX 预申报队列 |
| `beat` | 定时任务调度 |
| `mail-worker` | 客服邮件队列 |
| `tracking-worker` | 物流轨迹队列 |
| `tracking-history-worker` | 历史轨迹补查队列 |

前端构建后由 Nginx 托管 `frontend/dist`；详细服务器步骤见
[DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) 和 `deployment/isolated/README.md`。

## 环境变量

完整模板见 [.env.example](.env.example)，核心项：

| 变量 | 说明 |
| --- | --- |
| `SECRET_KEY` / `JWT_SECRET_KEY` | Flask / JWT 签名密钥，必须使用强随机值 |
| `ENCRYPTION_KEY` | 平台账号、店铺凭据的 Fernet 加密密钥，不可随意更换 |
| `DATABASE_URL` | 生产用 PostgreSQL；本地可留空用 SQLite |
| `REDIS_URL` | Celery broker / result backend |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 初始管理员 |
| `SHOPIFY_API_KEY` / `SHOPIFY_ACCESS_TOKEN` | Shopify 应用凭据（按店铺也可单独配置） |
| `SHOPIFY_OAUTH_CLIENT_ID/SECRET` | 需要读取客户姓名/地址等 PII 时使用 |
| `ZOHO_CLIENT_ID` / `ZOHO_CLIENT_SECRET` | 客服邮件（Zoho Mail） |
| `CORS_ORIGINS` | 允许的前端地址，逗号分隔 |
| `LOGISTICS_COST_AUTO` | 是否启用 4PX 真实物流费自动归集（默认 `true`） |
| `LOGISTICS_COST_CUTOVER` | 自动归集切换日，默认 `2026-11-01`，按订单日期计算 |
| `LOGISTICS_COST_BATCH` | 每次同步订单数，默认 `100` |
| `LOGISTICS_COST_MIN_AGE_HOURS` | 出库后多少小时才查 4PX 费用，默认 `24` |

> 注意：`ENCRYPTION_KEY` 更换会导致已有加密凭据无法解密；请妥善备份，或用
> `migrate_encryption_key.py` 迁移。

## 关键业务规则

### 库存与出库
- 库存是「公共库存」，按颜色/型号扣减，不预先绑定订单；
- 扫码出库时按实际面单扣库存；库存不足只扣现有数量，缺口记「账实差异」，不产生负库存；
- 同一客户多订单合并发货时，一个运单号可对应多个订单，分别扣各自商品库存；
- 售后补发面单条码（如加拿大智选：运单 `OLS…`、实体条码 `GV…`）会从 4PX 面单自动补齐。

### 采购清单
- 采购清单是人工确认过的**快照**：确认发起采购时不再因订单/售后变化拦截；
- 不修改清单商品和数量，多采购的数量到货后计入库存；
- 分批入库支持合格/货损/缺货，历史批次可追溯。

### 4PX 预申报
- 按目的地、件数、邮编、支架/手链套装规则建议物流产品；
- 加拿大智选邮编不可达时自动回退轻小件，并缓存被拒邮编；
- 建单为异步任务，可在页面查看进度，关页面不丢。

### 4PX 真实物流费
- 数据来自 `ds.xms.order.getFreight`（计费重 + 费用明细 + 总费用）；
- 自动按**订单日期**归集到订单费用，`source=4px`、状态 `pending`，不自动扣账户；
- 每天按订单日期自动生成/更新一条物流费账单（`4PX自动-YYYY-MM-DD`），一天一条；
- 不覆盖手工/历史账单费用；
- 历史账单（切换日前）保留在费用管理，不做订单级拆分。

### 权限
- 子账户按模块授权，后端强制校验（不只是隐藏菜单）；
- 店铺级隔离：所有查询/写入都会校验可访问店铺；
- 平台账号密码、店铺 4PX/Shopify 凭据使用 Fernet 加密存储；
- AI API 使用独立 API Key（`X-API-Key`），按 scope、店铺、IP 白名单、每日发送上限授权，并有调用审计。

## 测试与代码质量

```bash
# 后端
venv/bin/python -m pytest -q              # 全量
venv/bin/python -m pytest tests/test_orders.py -q

# 前端
cd frontend
npx tsc --noEmit
npx jest --runInBand
npm run build
```

分层约定：

| 层 | 目录 | 职责 |
| --- | --- | --- |
| 路由层 | `app/api/` | 只做参数解析与响应组装，不写 SQL、不写业务判断 |
| 服务层 | `app/services/` | 业务逻辑、事务边界、跨表操作 |
| 模型层 | `app/models/` | 表结构映射与轻量计算属性 |
| 公共层 | `app/utils/` | 鉴权、店铺范围、响应封装、异常定义 |

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/AI-API-生产接口文档.md](docs/AI-API-生产接口文档.md) | AI API 全部接口、示例、scope |
| [docs/AI-API-提示词.md](docs/AI-API-提示词.md) | 可直接复制给其他 AI 的系统提示词 |
| [docs/物流费用迁移-4PX真实费用.md](docs/物流费用迁移-4PX真实费用.md) | 从每日账单迁移到 4PX 按单费用 |
| [docs/物流费用迁移-账单覆盖匹配.md](docs/物流费用迁移-账单覆盖匹配.md) | 历史账单与订单覆盖匹配规则 |
| [docs/quantity-only-inventory.md](docs/quantity-only-inventory.md) | 数量库存模型 |
| [docs/logistics-monitoring.md](docs/logistics-monitoring.md) | 物流轨迹与自动补出库 |
| [docs/mail-ai-support-workflow.md](docs/mail-ai-support-workflow.md) | 客服邮件 AI 工作流 |
| [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) | 服务器部署、迁移、备份 |

## 常见问题

**Q：Shopify 订单里客户姓名/地址为空？**
Shopify Basic 套餐的 Admin API 不返回 PII，需要 Shopify/Advanced/Plus 套餐并申请客户数据权限，
或导入订单 CSV / 使用已获批 PII 权限的 Partner 应用换取令牌。

**Q：4PX 预申报点确认后一直 0%？**
任务在后台队列执行；如队列前有全量同步等长任务会等待。现在 4PX 预申报使用独立
`declaration` 队列和 worker，不再排在 Shopify 同步后面。

**Q：订单详情没有物流费？**
确认订单已出库且有 4PX 费用；自动任务按订单日期归集，出库后 4PX 出账才可查。
可在费用管理看到对应的 `4PX自动-日期` 账单。

**Q：为什么售后条码扫不出来？**
加拿大智选常见运单 `OLS…`、实体条码 `GV…`；系统会回查 4PX 面单补齐实体条码，
定时任务也会自动补。仍扫不出时检查售后单是否已绑定运单、状态是否已出库。

## License

内部业务系统，未开源授权；如需对外使用请联系项目维护者。
