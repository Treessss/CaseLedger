#!/bin/bash

# 案例账本生产环境部署脚本
# 使用方法: chmod +x deploy.sh && ./deploy.sh

set -e  # 遇到错误立即退出

echo "=== 案例账本生产环境部署脚本 ==="
echo "开始部署..."

# 检查是否为 root 用户
if [ "$EUID" -eq 0 ]; then
    echo "警告: 不建议使用 root 用户运行此脚本"
    read -p "是否继续? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        exit 1
    fi
fi

# 设置变量
APP_DIR="/opt/caseledger"
APP_USER="www-data"
CURRENT_DIR=$(pwd)

echo "当前目录: $CURRENT_DIR"
echo "目标目录: $APP_DIR"

# 检查必要的系统依赖
echo "检查系统依赖..."
command -v python3 >/dev/null 2>&1 || { echo "错误: 需要安装 Python 3"; exit 1; }
command -v node >/dev/null 2>&1 || { echo "错误: 需要安装 Node.js"; exit 1; }
command -v redis-cli >/dev/null 2>&1 || { echo "错误: 需要安装 Redis"; exit 1; }
command -v nginx >/dev/null 2>&1 || { echo "错误: 需要安装 Nginx"; exit 1; }

# 检查 Redis 是否运行
if ! redis-cli ping >/dev/null 2>&1; then
    echo "错误: Redis 服务未运行"
    echo "请运行: sudo systemctl start redis-server"
    exit 1
fi

echo "系统依赖检查通过"

# 创建应用目录
echo "创建应用目录..."
sudo mkdir -p $APP_DIR
sudo chown $USER:$USER $APP_DIR

# 复制文件到目标目录
echo "复制应用文件..."
cp -r . $APP_DIR/
cd $APP_DIR

# 删除不需要的文件
echo "清理不需要的文件..."
rm -rf venv/ __pycache__/ .git/ .gitignore
rm -f deploy.sh

# 创建虚拟环境
echo "创建 Python 虚拟环境..."
python3 -m venv venv
source venv/bin/activate

# 安装 Python 依赖
echo "安装 Python 依赖..."
pip install --upgrade pip
pip install -r requirements.txt

# 检查环境变量文件
if [ ! -f ".env" ]; then
    echo "创建环境变量文件..."
    cp .env.example .env
    echo "警告: 请编辑 .env 文件配置必要的环境变量"
    echo "特别是 Shopify API 配置和 SECRET_KEY"
fi

# 初始化数据库
echo "初始化数据库..."
python init_db.py

# 构建前端
echo "构建前端应用..."
cd frontend
npm install
npm run build
cd ..

# 设置文件权限
echo "设置文件权限..."
sudo chown -R $APP_USER:$APP_USER $APP_DIR
sudo chmod -R 755 $APP_DIR
sudo chmod 644 $APP_DIR/.env

# 创建 systemd 服务文件
echo "创建系统服务..."

# Flask 应用服务
sudo tee /etc/systemd/system/caseledger-app.service > /dev/null <<EOF
[Unit]
Description=CaseLedger Flask Application
After=network.target redis.service
Requires=redis.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python run.py
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF

# Celery Worker 服务
sudo tee /etc/systemd/system/caseledger-worker.service > /dev/null <<EOF
[Unit]
Description=CaseLedger Celery Worker
After=network.target redis.service
Requires=redis.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python start_celery_worker.py
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=30

[Install]
WantedBy=multi-user.target
EOF

# Celery Beat 服务
sudo tee /etc/systemd/system/caseledger-beat.service > /dev/null <<EOF
[Unit]
Description=CaseLedger Celery Beat Scheduler
After=network.target redis.service
Requires=redis.service

[Service]
Type=simple
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
Environment=PATH=$APP_DIR/venv/bin
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/python start_celery_beat.py
Restart=always
RestartSec=10
KillMode=mixed
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF

# 创建 Nginx 配置
echo "配置 Nginx..."
sudo tee /etc/nginx/sites-available/caseledger > /dev/null <<EOF
server {
    listen 80;
    server_name _;
    
    client_max_body_size 100M;
    
    # 前端静态文件
    location / {
        root $APP_DIR/frontend/dist;
        try_files \$uri \$uri/ /index.html;
        
        # 安全头
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header X-XSS-Protection "1; mode=block" always;
    }
    
    # API 代理
    location /api {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        
        # 超时设置
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
    
    # 静态资源缓存
    location ~* \.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)\$ {
        root $APP_DIR/frontend/dist;
        expires 1y;
        add_header Cache-Control "public, immutable";
        access_log off;
    }
    
    # 禁止访问敏感文件
    location ~ /\. {
        deny all;
        access_log off;
        log_not_found off;
    }
    
    location ~ \.(env|py|pyc|pyo|db)\$ {
        deny all;
        access_log off;
        log_not_found off;
    }
}
EOF

# 启用 Nginx 站点
if [ ! -L "/etc/nginx/sites-enabled/caseledger" ]; then
    sudo ln -s /etc/nginx/sites-available/caseledger /etc/nginx/sites-enabled/
fi

# 删除默认站点（如果存在）
if [ -L "/etc/nginx/sites-enabled/default" ]; then
    sudo rm /etc/nginx/sites-enabled/default
fi

# 测试 Nginx 配置
echo "测试 Nginx 配置..."
sudo nginx -t

# 重新加载 systemd
echo "重新加载系统服务..."
sudo systemctl daemon-reload

# 启动服务
echo "启动应用服务..."
sudo systemctl enable caseledger-app caseledger-worker caseledger-beat
sudo systemctl start caseledger-app caseledger-worker caseledger-beat

# 重启 Nginx
echo "重启 Nginx..."
sudo systemctl restart nginx

# 检查服务状态
echo "检查服务状态..."
sleep 5

echo "=== 服务状态 ==="
echo "Flask 应用:"
sudo systemctl is-active caseledger-app || echo "❌ Flask 应用未运行"

echo "Celery Worker:"
sudo systemctl is-active caseledger-worker || echo "❌ Celery Worker 未运行"

echo "Celery Beat:"
sudo systemctl is-active caseledger-beat || echo "❌ Celery Beat 未运行"

echo "Nginx:"
sudo systemctl is-active nginx || echo "❌ Nginx 未运行"

echo "Redis:"
sudo systemctl is-active redis-server || echo "❌ Redis 未运行"

echo ""
echo "=== 部署完成 ==="
echo "应用已部署到: $APP_DIR"
echo "访问地址: http://$(hostname -I | awk '{print $1}')"
echo ""
echo "默认管理员账户:"
echo "用户名: admin"
echo "密码: qweasdzxc"
echo ""
echo "重要提醒:"
echo "1. 请编辑 $APP_DIR/.env 文件配置 Shopify API"
echo "2. 首次登录后请立即修改默认密码"
echo "3. 建议配置 SSL 证书以启用 HTTPS"
echo "4. 定期备份数据库文件"
echo ""
echo "查看日志命令:"
echo "sudo journalctl -u caseledger-app -f"
echo "sudo journalctl -u caseledger-worker -f"
echo "sudo journalctl -u caseledger-beat -f"
echo ""
echo "如有问题，请查看部署指南: $APP_DIR/DEPLOYMENT_GUIDE.md"