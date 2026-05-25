#!/bin/bash

# CaseLedger 快速启动脚本
# 一键安装并启动所有服务

echo "=== CaseLedger 快速启动 ==="
echo

# 检查 Redis 是否运行
echo "检查 Redis 服务..."
if ! redis-cli ping > /dev/null 2>&1; then
    echo "❌ Redis 未运行，请先启动 Redis:"
    echo "   brew services start redis"
    echo "   或者: redis-server"
    exit 1
fi
echo "✅ Redis 运行正常"

# 检查 Python 依赖
echo "检查 Python 依赖..."
if ! python3 -c "import flask, celery, redis" > /dev/null 2>&1; then
    echo "❌ Python 依赖缺失，请安装:"
    echo "   pip3 install -r requirements.txt"
    exit 1
fi
echo "✅ Python 依赖正常"

# 检查 Node.js 和前端依赖
echo "检查 Node.js 和前端依赖..."
if ! command -v npm &> /dev/null; then
    echo "❌ npm 未安装，请先安装 Node.js"
    exit 1
fi
if [ ! -d "frontend/node_modules" ]; then
    echo "❌ 前端依赖未安装，请运行:"
    echo "   cd frontend && npm install"
    exit 1
fi
echo "✅ Node.js 和前端依赖正常"

# 创建日志目录
echo "创建日志目录..."
mkdir -p logs
echo "✅ 日志目录已创建"

# 安装并启动服务
echo "安装并启动服务..."
./manage_services.sh install
./manage_services.sh start

echo
echo "=== 启动完成 ==="
echo "Flask 后端: http://127.0.0.1:5000"
echo "前端应用: http://127.0.0.1:8000"
echo "查看状态: ./manage_services.sh status"
echo "查看日志: ./manage_services.sh logs"
echo "停止服务: ./manage_services.sh stop"
echo
echo "服务已在后台持久运行，重启电脑后会自动启动。"