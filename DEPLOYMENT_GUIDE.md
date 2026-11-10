# 案例账本 - 服务器部署指南

## 系统要求

- Python 3.10（与本项目 CI 一致）
- Node.js 16+
- Redis 服务器
- PostgreSQL 14+（生产环境必需；SQLite 仅用于本地零配置体验）
- Nginx (推荐)

## 本地开发环境（macOS + PostgreSQL）

macOS 自带的 5000 端口常被 AirPlay 接收器占用，且本机可能已有 PostgreSQL 实例，
推荐按下述方式起一套独立环境（以 Homebrew 为例）：

```bash
# 1. 启动 PostgreSQL（可用 brew services 让它在开机时自启）
brew services start postgresql@14

# 2. 建专用角色与库（不要复用你其它项目的库）
psql -d postgres -c "CREATE ROLE caseledger LOGIN PASSWORD '本地密码';"
psql -d postgres -c "CREATE DATABASE caseledger OWNER caseledger ENCODING 'UTF8';"

# 3. 后端依赖与配置
python3.10 -m venv venv && ./venv/bin/pip install -r requirements-dev.txt
# 编辑 .env：DATABASE_URL=postgresql://caseledger:本地密码@127.0.0.1:5432/caseledger

# 4. 建表 + 初始化默认数据
./venv/bin/flask db upgrade
./venv/bin/python init_db.py

# 5. 若需要历史数据，从 SQLite 搬过来
./venv/bin/python migrate_to_postgres.py \
    --source sqlite:////绝对路径/instance/caseLedger.db
```

启动服务（后端换到 5010，避免与已占用端口或其它实例冲突）：

```bash
# 后端
HOST=127.0.0.1 PORT=5010 ./venv/bin/python run.py

# 前端（另开一个终端；8002 已在本项目 CORS 白名单内）
cd frontend && PORT=8002 API_TARGET=http://127.0.0.1:5010 npm run start:dev
```

> 同一台机器上跑多份代码副本时，请确认每份用的 `.env` 指向各自的数据库：
> `lsof -p $(lsof -nP -iTCP:5010 -sTCP:LISTEN -t) | grep '\.db'` 可以看到进程实际打开的文件。

## 数据库结构变更规范（重要）

**任何表/字段变更都必须生成 Alembic 迁移，不能只写一个临时脚本改库。**

原因（2026-09 实际踩坑）：给 `expenses` 加「收入」字段、给 `accounts` 加「期初余额」时，
是用一次性脚本 `ALTER TABLE` 改的库，模型和本地库都对，但 **Alembic 基线里没有这两列**。
结果按部署流程在服务器上执行 `flask db upgrade` 建库时，得到的是缺列的表，
数据迁移直接失败：`column "opening_balance" does not exist`。

正确做法：

```bash
# 1. 改 app/models/*.py
# 2. 生成迁移（会自动对比模型与当前库的差异）
flask db migrate -m "说明这次改了什么"
# 3. 检查生成的 migrations/versions/*.py：
#    - 给「已有数据的表」加 NOT NULL 列必须带 server_default，否则老库升级会失败
#    - 确认没有误删/误建无关对象
# 4. 应用
flask db upgrade
# 5. 在空库上验证一遍（这是唯一能发现 schema 漂移的方法）
```

**每次改完模型都要做的自检**：新库上 `flask db upgrade` 建出的表，必须与模型完全一致。
本次就是靠「空库迁移 → 迁数据」这条链路才发现问题的。

## 部署步骤

### 1. 服务器准备

```bash
# 更新系统包
sudo apt update && sudo apt upgrade -y

# 安装必要的系统依赖
sudo apt install -y python3 python3-pip python3-venv nodejs npm redis-server nginx git

# 启动 Redis 服务
sudo systemctl start redis-server
sudo systemctl enable redis-server
```

### 2. 代码部署

```bash
# 克隆代码到服务器
git clone <your-repository-url> /opt/caseledger
cd /opt/caseledger

# 创建 Python 虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
```

### 3. 环境配置

```bash
# 复制环境变量文件
cp .env.example .env

# 编辑环境变量
nano .env
```

**重要环境变量配置：**
```env
# Flask 配置
FLASK_ENV=production
FLASK_DEBUG=0
SECRET_KEY=your-secret-key-here

# 数据库配置（生产环境使用 PostgreSQL）
DATABASE_URL=postgresql://caseledger:强密码@127.0.0.1:5432/caseledger

# 连接池（可选，默认值已适配单机部署）
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20

# Redis 配置
REDIS_URL=redis://localhost:6379/0

# Shopify 配置
SHOPIFY_API_KEY=your-shopify-api-key
SHOPIFY_API_SECRET=your-shopify-api-secret
SHOPIFY_ACCESS_TOKEN=your-shopify-access-token
SHOPIFY_SHOP_DOMAIN=your-shop.myshopify.com

# JWT 配置
JWT_SECRET_KEY=your-jwt-secret-key
# 登录后免登录时长（小时）：168 = 7 天，公网环境建议 12~24
JWT_EXPIRES_HOURS=168
```

### 4. 数据库初始化

```bash
# 1. 准备 PostgreSQL（首次部署）
sudo apt install -y postgresql postgresql-contrib
sudo -u postgres psql -c "CREATE ROLE caseledger LOGIN PASSWORD '强密码';"
sudo -u postgres psql -c "CREATE DATABASE caseledger OWNER caseledger ENCODING 'UTF8';"

# 2. 建表 / 升级表结构（幂等，可反复执行）
flask db upgrade

# 3. 初始化默认数据（手续费配置等，幂等）
python init_db.py

# 4. 若要把本地 SQLite 的历史数据搬过来（在本地执行，指向服务器库）
python migrate_to_postgres.py \
    --source sqlite:////绝对路径/instance/caseLedger.db \
    --target postgresql://caseledger:强密码@服务器地址:5432/caseledger
```

> 迁移脚本会按外键顺序复制数据，并逐表核对行数、核对订单/支付/费用的金额合计，
> 任一项不一致都会以非 0 退出码结束。目标库非空时必须加 `--recreate`（会先重建表结构）。

### 5. 前端构建

```bash
cd frontend

# 安装前端依赖
npm install

# 构建生产版本
npm run build

# 返回根目录
cd ..
```

### 6. 系统服务配置

#### 创建 Flask 应用服务

```bash
sudo nano /etc/systemd/system/caseledger-app.service
```

```ini
[Unit]
Description=CaseLedger Flask Application
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/caseledger
Environment=PATH=/opt/caseledger/venv/bin
EnvironmentFile=/opt/caseledger/.env
# 生产环境用 gunicorn（Flask 开发服务器不具备生产可用性与安全性）
ExecStart=/opt/caseledger/venv/bin/gunicorn --workers 4 --worker-class gthread --threads 8 --timeout 120 --bind 127.0.0.1:5000 run:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 创建 Celery Worker 服务

```bash
sudo nano /etc/systemd/system/caseledger-worker.service
```

```ini
[Unit]
Description=CaseLedger Celery Worker
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/caseledger
Environment=PATH=/opt/caseledger/venv/bin
ExecStart=/opt/caseledger/venv/bin/python start_celery_worker.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### 创建 Celery Beat 服务

```bash
sudo nano /etc/systemd/system/caseledger-beat.service
```

```ini
[Unit]
Description=CaseLedger Celery Beat Scheduler
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/caseledger
Environment=PATH=/opt/caseledger/venv/bin
ExecStart=/opt/caseledger/venv/bin/python start_celery_beat.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### 7. Nginx 配置

```bash
sudo nano /etc/nginx/sites-available/caseledger
```

```nginx
server {
    listen 80;
    server_name your-domain.com;

    # 前端静态文件
    location / {
        root /opt/caseledger/frontend/dist;
        try_files $uri $uri/ /index.html;
    }

    # API 代理
    location /api {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg)$ {
        root /opt/caseledger/frontend/dist;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
}
```

```bash
# 启用站点
sudo ln -s /etc/nginx/sites-available/caseledger /etc/nginx/sites-enabled/

# 测试 Nginx 配置
sudo nginx -t

# 重启 Nginx
sudo systemctl restart nginx
```

### 8. 启动服务

```bash
# 重新加载 systemd
sudo systemctl daemon-reload

# 启动并启用服务
sudo systemctl start caseledger-app
sudo systemctl enable caseledger-app

sudo systemctl start caseledger-worker
sudo systemctl enable caseledger-worker

sudo systemctl start caseledger-beat
sudo systemctl enable caseledger-beat

# 检查服务状态
sudo systemctl status caseledger-app
sudo systemctl status caseledger-worker
sudo systemctl status caseledger-beat
```

### 9. SSL 证书配置 (可选)

```bash
# 安装 Certbot
sudo apt install certbot python3-certbot-nginx

# 获取 SSL 证书
sudo certbot --nginx -d your-domain.com

# 设置自动续期
sudo crontab -e
# 添加以下行：
# 0 12 * * * /usr/bin/certbot renew --quiet
```

## 监控和维护

### 日志查看

```bash
# 查看应用日志
sudo journalctl -u caseledger-app -f

# 查看 Worker 日志
sudo journalctl -u caseledger-worker -f

# 查看 Beat 日志
sudo journalctl -u caseledger-beat -f

# 查看 Nginx 日志
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log
```

### 服务管理

```bash
# 重启服务
sudo systemctl restart caseledger-app
sudo systemctl restart caseledger-worker
sudo systemctl restart caseledger-beat

# 停止服务
sudo systemctl stop caseledger-app
sudo systemctl stop caseledger-worker
sudo systemctl stop caseledger-beat
```

### 数据备份

```bash
# 备份数据库（自动识别 PostgreSQL / SQLite）
cd /opt/caseledger
./scripts/backup_db.sh 30          # 参数为保留天数，默认 30

# 备份配置文件
cp /opt/caseledger/.env /backup/.env_$(date +%Y%m%d_%H%M%S)
```

`deploy.sh` 已内置 `caseledger-backup.timer`，服务器上每天 03:00 自动执行备份，
只在 `backups/` 下清理本脚本自己生成的 `auto_*` 文件，手工备份不会被删。

恢复方式：

```bash
# PostgreSQL（custom 格式）
pg_restore --clean --no-owner -d "postgresql://caseledger:密码@127.0.0.1:5432/caseledger" \
    backups/auto_caseledger_pg_YYYYmmdd_HHMMSS.dump

# SQLite：停服务后直接替换文件
```

### 账户对账（财务数据巡检）

```bash
python scripts/reconcile_accounts.py           # 余额是否等于「期初 + 已完成充值 - 消耗」
python scripts/reconcile_accounts.py --detail 9  # 查某个账户的最近流水，定位差额
```

存在 `ANOMALY`（余额低于流水应有金额）时退出码为 1，可挂到定时任务做巡检。

## 故障排除

### 常见问题

1. **服务无法启动**
   - 检查环境变量配置
   - 确认 Redis 服务运行正常
   - 查看服务日志定位问题

2. **Shopify 同步失败**
   - 验证 Shopify API 凭据
   - 检查网络连接
   - 确认 Celery Worker 正常运行

3. **前端页面无法访问**
   - 检查 Nginx 配置
   - 确认前端文件已正确构建
   - 验证代理设置

### 性能优化

1. **数据库优化**
   - 定期清理过期数据
   - 添加必要的索引
   - 生产环境使用 PostgreSQL（见「数据库结构变更规范」与 README 数据库章节）
   - 报表接口已消除 N+1 查询（30 天区间约 5 条 SQL），不要退回逐日循环查库

2. **缓存优化**
   - 配置 Redis 持久化
   - 调整缓存过期时间
   - 监控内存使用情况

3. **服务器优化**
   - 调整 Nginx worker 进程数
   - 配置适当的文件描述符限制
   - 监控系统资源使用情况

## 安全建议

1. **防火墙配置**
   ```bash
   sudo ufw allow ssh
   sudo ufw allow 'Nginx Full'
   sudo ufw enable
   ```

2. **定期更新**
   - 定期更新系统包
   - 更新 Python 依赖
   - 监控安全漏洞

3. **访问控制**
   - 使用强密码
   - 配置 SSH 密钥认证
   - 限制管理员访问

---

**部署完成后，访问 http://your-domain.com 即可使用案例账本系统！**

默认管理员账户：

管理员账号不再硬编码在代码中，请在 `.env` 中通过以下变量配置：

```env
ADMIN_USERNAME=admin
ADMIN_PASSWORD=请设置一个强密码
# 或使用哈希（推荐）: ADMIN_PASSWORD_HASH=...
```

**注意：历史默认密码 admin/qweasdzxc 已随代码公开，切勿继续使用。**
