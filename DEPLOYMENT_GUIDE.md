# 案例账本 - 服务器部署指南

## 系统要求

- Python 3.8+
- Node.js 16+
- Redis 服务器
- SQLite 或 PostgreSQL 数据库
- Nginx (推荐)

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

# 数据库配置
DATABASE_URL=sqlite:///instance/caseLedger.db

# Redis 配置
REDIS_URL=redis://localhost:6379/0

# Shopify 配置
SHOPIFY_API_KEY=your-shopify-api-key
SHOPIFY_API_SECRET=your-shopify-api-secret
SHOPIFY_ACCESS_TOKEN=your-shopify-access-token
SHOPIFY_SHOP_DOMAIN=your-shop.myshopify.com

# JWT 配置
JWT_SECRET_KEY=your-jwt-secret-key
```

### 4. 数据库初始化

```bash
# 初始化数据库
python init_db.py

# 运行数据库迁移
flask db upgrade
```

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
ExecStart=/opt/caseledger/venv/bin/python run.py
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
# 备份数据库
cp /opt/caseledger/instance/caseLedger.db /backup/caseLedger_$(date +%Y%m%d_%H%M%S).db

# 备份配置文件
cp /opt/caseledger/.env /backup/.env_$(date +%Y%m%d_%H%M%S)
```

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
   - 考虑使用 PostgreSQL 替代 SQLite

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
- 用户名：admin
- 密码：qweasdzxc

**请在首次登录后立即修改默认密码！**