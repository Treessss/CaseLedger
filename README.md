# CaseLedger - Shopify手机壳财务管理系统

一个专为Shopify手机壳店铺设计的财务管理系统，支持自动同步订单数据、计算各种手续费、管理运营成本并生成详细的财务报表。

## 功能特性

### 📊 订单管理
- 自动从Shopify同步订单数据
- 支持PayPal和Stripe支付方式
- 自动计算支付手续费（固定费用+百分比费用）
- 计算Shopify平台手续费
- 实时计算实际到账金额和利润率

### 💰 费用管理
- Facebook广告费用记录
- 方果工厂手机壳成本管理
- 4PX物流费用跟踪
- 支持批量导入和手动添加
- 费用分类和供应商管理

### 📈 财务分析
- 收入趋势分析
- 利润率统计
- 成本结构分析
- 日报、月报、年报生成
- 支持Excel导出

### ⚙️ 系统配置
- PayPal/Stripe手续费率配置
- Shopify API集成设置
- 自动同步任务调度
- 灵活的费用类别管理

## 技术栈

- **后端**: Python Flask
- **数据库**: MySQL
- **前端**: Bootstrap 5 + jQuery
- **图表**: Chart.js
- **任务队列**: Celery + Redis
- **API集成**: Shopify API, PayPal API, Stripe API

## 安装部署

### 1. 环境要求

- Python 3.8+
- MySQL 5.7+
- Redis (用于Celery任务队列)

### 2. 克隆项目

```bash
git clone <repository-url>
cd CaseLedger
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

复制环境变量模板并配置：

```bash
cp .env.example .env
```

编辑 `.env` 文件，配置以下信息：

```env
# 数据库配置
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_mysql_password
MYSQL_DB=case_ledger

# Shopify API配置
SHOPIFY_API_KEY=your_shopify_api_key
SHOPIFY_API_SECRET=your_shopify_api_secret
SHOPIFY_ACCESS_TOKEN=your_shopify_access_token
SHOPIFY_SHOP_URL=your-shop-name.myshopify.com

# PayPal配置
PAYPAL_CLIENT_ID=your_paypal_client_id
PAYPAL_CLIENT_SECRET=your_paypal_client_secret
PAYPAL_MODE=sandbox  # 生产环境改为 live

# Stripe配置
STRIPE_SECRET_KEY=your_stripe_secret_key
STRIPE_PUBLISHABLE_KEY=your_stripe_publishable_key
```

### 5. 初始化数据库

```bash
python init_db.py
```

### 6. 启动应用

```bash
python app.py
```

应用将在 `http://localhost:5000` 启动。

### 7. 启动后台任务（可选）

如需自动同步功能，启动Celery worker：

```bash
# 启动Redis
redis-server

# 启动Celery worker
celery -A app.celery worker --loglevel=info

# 启动Celery beat（定时任务）
celery -A app.celery beat --loglevel=info
```

## 使用指南

### 1. 首次配置

1. 访问系统设置页面配置手续费率
2. 确认Shopify API连接正常
3. 测试PayPal/Stripe API连接

### 2. 数据同步

- 手动同步：在仪表板点击"同步订单"按钮
- 自动同步：配置Celery定时任务

### 3. 费用管理

- 在费用管理页面添加各类运营费用
- 支持Excel批量导入
- 可按日期范围筛选和统计

### 4. 报表查看

- 仪表板提供实时概览
- 报表页面提供详细分析
- 支持导出Excel格式

## API文档

### 订单相关

- `GET /api/orders` - 获取订单列表
- `POST /api/sync/orders` - 同步Shopify订单
- `GET /api/orders/recent` - 获取最近订单

### 费用相关

- `GET /api/expenses` - 获取费用列表
- `POST /api/expenses` - 添加费用记录
- `PUT /api/expenses/<id>` - 更新费用记录
- `DELETE /api/expenses/<id>` - 删除费用记录

### 报表相关

- `GET /api/reports/revenue` - 收入报表
- `GET /api/reports/profit` - 利润报表
- `GET /api/reports/expenses` - 费用报表
- `GET /api/reports/export` - 导出Excel报表

### 配置相关

- `GET /api/settings/fees` - 获取手续费配置
- `PUT /api/settings/fees/<id>` - 更新手续费配置

## 数据库结构

### 主要表结构

- `orders` - 订单表
- `payments` - 支付记录表
- `expenses` - 费用支出表
- `fee_configs` - 手续费配置表
- `products` - 商品表

## 开发说明

### 项目结构

```
CaseLedger/
├── app/
│   ├── __init__.py
│   ├── models/          # 数据模型
│   ├── api/             # API路由
│   ├── main/            # 主要路由
│   ├── services/        # 业务逻辑
│   ├── utils/           # 工具函数
│   ├── templates/       # HTML模板
│   └── static/          # 静态文件
├── config.py            # 配置文件
├── app.py              # 应用入口
├── init_db.py          # 数据库初始化
├── requirements.txt    # 依赖包
└── README.md          # 说明文档
```

### 代码规范

- 遵循PEP 8代码规范
- 使用类型注解
- 编写单元测试
- 添加适当的注释和文档

## 常见问题

### Q: Shopify API连接失败
A: 检查API密钥和访问令牌是否正确，确认店铺URL格式正确。

### Q: 手续费计算不准确
A: 在系统设置中检查PayPal/Stripe手续费率配置是否与实际一致。

### Q: 数据同步缓慢
A: 可以配置Celery后台任务来异步处理大量数据同步。

### Q: 报表数据为空
A: 确认已同步订单数据，并检查日期筛选条件。

## 许可证

MIT License

## 联系方式

如有问题或建议，请提交Issue或联系开发团队。