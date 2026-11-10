#!/bin/bash

# CaseLedger 内网穿透启动脚本
# 为前端和后端服务创建公网访问隧道

# 安全闸门：内网穿透会把本机的订单、客户、财务数据直接发布到公网固定域名，
# 而本地服务默认不带任何访问控制，因此默认禁止执行，必须显式确认。
if [ "${ALLOW_PUBLIC_TUNNEL:-no}" != "yes" ]; then
    cat <<'EOT'
⚠️  已阻止执行：内网穿透会将服务直接暴露到公网。

执行前请确认：
  1) 后端 FLASK_DEBUG 必须为 False（调试模式会暴露可执行代码的调试器）；
  2) 只使用临时测试数据，不要使用真实的订单/客户/财务数据；
  3) 演示结束后立即停止隧道（Ctrl+C 或 kill 对应进程）。

确认无误后执行：  ALLOW_PUBLIC_TUNNEL=yes ./start_tunnel.sh
EOT
    exit 1
fi

echo "=== CaseLedger 内网穿透服务 ==="
echo

# 检查服务是否运行
echo "检查本地服务状态..."
if ! curl -s http://127.0.0.1:8000 > /dev/null; then
    echo "❌ 前端服务未运行，请先启动服务:"
    echo "   ./manage_services.sh start"
    exit 1
fi
echo "✅ 前端服务运行正常"

if ! curl -s http://127.0.0.1:5000 > /dev/null; then
    echo "❌ 后端服务未运行，请先启动服务:"
    echo "   ./manage_services.sh start"
    exit 1
fi
echo "✅ 后端服务运行正常"

echo
echo "启动内网穿透隧道..."
echo

# 启动前端隧道
echo "启动前端隧道 (端口 8000)..."
lt --port 8000 --subdomain caseledger-frontend &
FRONTEND_PID=$!
echo "前端隧道 PID: $FRONTEND_PID"

# 等待一下让隧道建立
sleep 3

# 启动后端隧道
echo "启动后端隧道 (端口 5000)..."
lt --port 5000 --subdomain caseledger-backend &
BACKEND_PID=$!
echo "后端隧道 PID: $BACKEND_PID"

# 等待隧道建立
sleep 5

echo
echo "=== 隧道已建立 ==="
echo "前端访问地址: https://caseledger-frontend.loca.lt"
echo "后端访问地址: https://caseledger-backend.loca.lt"
echo
echo "注意: 首次访问可能需要点击 'Click to Continue' 按钮"
echo
echo "停止隧道: kill $FRONTEND_PID $BACKEND_PID"
echo "或者按 Ctrl+C 停止此脚本"
echo

# 保存PID到文件
echo $FRONTEND_PID > /tmp/caseledger_frontend_tunnel.pid
echo $BACKEND_PID > /tmp/caseledger_backend_tunnel.pid

# 等待用户中断
trap 'echo "\n正在停止隧道..."; kill $FRONTEND_PID $BACKEND_PID 2>/dev/null; rm -f /tmp/caseledger_*_tunnel.pid; exit 0' INT

echo "隧道正在运行中，按 Ctrl+C 停止..."
wait
