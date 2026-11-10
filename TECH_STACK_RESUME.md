# CaseLedger 技术栈文档（简历模式）

## 1. 项目定位（一句话）
CaseLedger 是一个面向 Shopify 电商场景的财务管理系统，覆盖订单同步、费用管理、利润核算、账户管理与经营报表分析。

## 2. 技术栈总览（简历写法）

| 技术层 | 技术选型 | 简历表达建议 |
| --- | --- | --- |
| 后端框架 | Python, Flask, Flask Blueprint | 搭建模块化后端服务，按领域拆分 API 路由，支持订单、费用、报表、账户、系统配置等核心业务。 |
| 数据访问 | SQLAlchemy, Flask-Migrate | 基于 ORM 完成模型设计与查询优化，支持分页、条件筛选、聚合统计与迁移管理。 |
| 数据库 | SQLite（默认）, SQLAlchemy `DATABASE_URL` 可扩展（当前依赖含 PyMySQL） | 开发环境默认 SQLite，生产可通过环境变量切换数据库连接。 |
| 鉴权与安全 | JWT（PyJWT）, `before_request` 鉴权中间件, Flask-CORS | 统一 API Token 鉴权，前后端分离场景下处理跨域与认证链路。 |
| 异步任务 | Celery 5, Redis, Celery Beat | 构建定时+异步同步机制，实现 Shopify 订单/商品同步、连接巡检与失败重试。 |
| 第三方集成 | Shopify API, Stripe SDK, PayPal SDK, Requests | 打通电商订单与支付链路，支持外部平台数据拉取与财务数据整合。 |
| 前端框架 | React 19, TypeScript 5, Umi Max 4 | 使用工程化前端框架构建管理后台，按业务域组织页面与 API Service 层。 |
| UI 体系 | Ant Design 5, Ant Design Pro Components | 基于 ProTable、PageContainer 等组件快速搭建高可维护后台页面。 |
| 前端请求层 | `@umijs/max` request, proxy 配置 | 建立统一请求与错误处理机制，支持开发环境代理与多环境配置。 |
| 工程化 | Biome, TypeScript 检查, Jest, Husky, lint-staged | 建立前端代码规范、静态检查与基础自动化测试流程。 |
| 部署运维 | Nginx, systemd（app/worker/beat） | 支持前后端分离部署、反向代理与任务进程常驻化管理。 |

## 3. 架构能力点（可写进简历的“做过什么”）

- 采用 Flask App Factory + Blueprint + Service Layer 结构，降低路由层复杂度，提升业务模块可维护性。
- 在同步链路中引入 Celery 重试机制与数据库锁冲突重试策略，提升批量同步稳定性。
- 基于 SQLAlchemy 实现订单、费用、账户、平台账号等模型体系，支持多维筛选与财务聚合统计。
- 在报表侧实现按日/周/月/季度/年度口径的财务分析逻辑，并支持多币种统一换算（CNY）口径输出。
- 前端基于 Umi Max + Ant Design Pro 建立后台管理界面，形成页面层、服务层、类型层分离的实现方式。

## 4. 简历可直接复制版本

### 4.1 精简版（1-2 行）
负责 CaseLedger 财务管理系统全栈开发，技术栈覆盖 Flask + SQLAlchemy + Celery + Redis + React + Umi Max + Ant Design Pro，完成 Shopify 订单同步、费用核算、利润分析与运营报表模块建设。

### 4.2 要点版（3 条）
- 搭建 Flask + SQLAlchemy 后端架构，完成订单/费用/报表/账户等核心 API 与鉴权中间件设计。  
- 基于 Celery + Redis 实现 Shopify 数据定时同步与异步处理，支持重试与任务调度。  
- 使用 React 19 + Umi Max + Ant Design Pro 开发管理后台，实现财务看板、明细管理、配置中心等页面。

### 4.3 关键词版（ATS）
`Python` `Flask` `SQLAlchemy` `Flask-Migrate` `JWT` `Celery` `Redis` `Shopify API` `Stripe` `PayPal` `React` `TypeScript` `Umi Max` `Ant Design Pro` `Jest` `Nginx`
