# AGENTS.md

## 1) 项目概览

CaseLedger 是一个财务管理系统：
- 后端：Flask + SQLAlchemy + Celery
- 前端：Umi/Ant Design Pro（`frontend/`）
- 默认数据库：SQLite（可通过 `DATABASE_URL` 切换）
- 异步任务：Redis + Celery

## 2) 目录约定

- `run.py`：后端启动入口
- `init_db.py`：数据库初始化脚本
- `app/api/`：后端 API 路由
- `app/models/`：数据库模型
- `app/services/`：业务服务层
- `celery_app.py`：Celery 配置与定时任务
- `start_celery_worker.py` / `start_celery_beat.py`：Celery 启动脚本
- `frontend/`：前端工程（Node.js）

## 3) 本地初始化（Init）

### 后端

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python init_db.py
python run.py
```

后端默认监听：`http://127.0.0.1:5000`

### 前端

```bash
cd frontend
npm install
npm run start:dev
```

前端默认开发端口通常为：`8000`（由 Umi 启动）

## 4) 常用开发命令

### 后端

```bash
python run.py
python init_db.py
python start_celery_worker.py
python start_celery_beat.py
```

### 前端

```bash
cd frontend
npm run start:dev
npm run lint
npm test
npm run build
```

## 5) 数据库与迁移约定

- 运行时以 `config.py` 为准：优先读取 `DATABASE_URL`，未设置时使用本地 SQLite 文件。
- 生产环境使用 **PostgreSQL**（`DATABASE_URL=postgresql://...`）；SQLite 仅用于本地零配置启动。
- 修改模型或迁移前，先备份数据库：`./scripts/backup_db.sh`（自动识别 PostgreSQL/SQLite）。

**表结构变更必须生成 Alembic 迁移**（这是硬性要求，不是建议）：

```bash
# 改模型后
flask db migrate -m "说明这次改了什么"
flask db upgrade
```

- 禁止只写一次性脚本 `ALTER TABLE` 就完事：模型和本地库会对，但迁移里没有，
  别人（或服务器）按 `flask db upgrade` 建出来的库就是缺列的。
  → 2026-09 因为这样漏了两列，服务器建库后数据迁移直接失败。
- 给「已有数据的表」加 NOT NULL 列时，迁移里必须带 `server_default`，否则老库升级失败。
- 每次改完模型都要在**空库**上跑一遍 `flask db upgrade`，确认建出的结构与模型一致。

现有的历史数据迁移脚本（一次性使用，不是日常流程）：

| 脚本 | 用途 |
| --- | --- |
| `migrate_to_postgres.py` | SQLite → PostgreSQL，搬迁并核对行数与金额 |
| `migrate_add_multi_shop.py` | 单店铺 → 多店铺（建表、加 shop_id、回填） |
| `migrate_add_income_records.py` | 费用表加「收入」字段 |
| `migrate_add_account_opening_balance.py` | 账户加期初余额并回填 |

## 6) 开发规则

- **业务逻辑一律放 `app/services/`**，路由层（`app/api/`）只做参数解析与响应组装：
  - 路由里不出现 `db.session`、不写查询、不写事务；
  - 成功响应用 `app.utils.responses.ok(...)`（保持 `{success, data, message}` 信封）；
  - 业务错误直接抛 `app.utils.errors` 里的异常（`ValidationError` / `NotFoundError` /
    `ForbiddenError`），由全局 errorhandler 统一渲染，不要在路由里写 try/except。
- 新增接口时同步更新前端 `frontend/src/services/` 对应调用。
- **新增接口要挂到功能权限表**：`app/utils/permissions.py` 的 `ROUTE_PERMISSIONS` 决定
  某个路径需要哪个模块权限，`ALWAYS_ALLOWED` 是登录即可的例外。忘了登记 = 任何登录用户都能调，
  相当于权限漏洞；前端菜单项同样要在 `frontend/config/routes.ts` 上写对应的 `access`。
- **时间统一口径**：写入用 `datetime.utcnow()`（禁止 `datetime.now()`），展示用
  `frontend/src/utils/timezone.ts` 的 `formatBeijingTime`（UTC → 北京时间）；
  纯日期字段（如 `expense_date`）按业务日期原样处理，不做时区换算。
- 前端页面不要直接调用 `request()`，统一走 `frontend/src/services/`；
  错误提示用 `@/utils/error` 的 `getErrorMessage`。
- 提交前至少完成：
  - 后端 `make test`（pytest）与 `make lint`（ruff）全绿
  - 前端 `npm run tsc` 与 `npm run biome:lint` 通过
  - 若有模型改动：在空库上验证过 `flask db upgrade`

## 7) 已知注意点

- API 鉴权逻辑位于 `app/__init__.py` 的 `before_request`，调试接口时注意携带 `Authorization: Bearer <token>`。
- 订单等业务数据里的时间戳按 **UTC** 存储；报表在**未传日期**时的默认区间用的是服务器本地日期，
  跨零点（尤其北京时间每月 1 号 00:00–08:00）可能少统计，前端带日期查询不受影响。
- 同一台机器跑多份代码副本时，先确认进程实际连的是哪个库：
  `lsof -p $(lsof -nP -iTCP:5010 -sTCP:LISTEN -t) | grep '\.db'`。
