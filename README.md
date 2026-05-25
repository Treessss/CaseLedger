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

复制 `.env.example` 为 `.env` 后配置：

```env
FLASK_ENV=development
FLASK_DEBUG=True
SECRET_KEY=your-secret-key

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
