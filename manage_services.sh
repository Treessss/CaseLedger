#!/bin/bash

# CaseLedger 服务管理脚本
# 用于在 macOS 上管理 CaseLedger 的持久化运行

PROJECT_DIR="/Users/Zhuanz/code/script/CaseLedger"
LAUNCH_AGENTS_DIR="$HOME/Library/LaunchAgents"

# 服务列表
SERVICES=("com.caseledger.app" "com.caseledger.worker" "com.caseledger.full-worker" "com.caseledger.beat" "com.caseledger.frontend")

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# 安装服务
install_services() {
    print_status "安装 CaseLedger 服务..."
    
    # 复制 plist 文件到 LaunchAgents 目录
    for service in "${SERVICES[@]}"; do
        cp "$PROJECT_DIR/launchd/$service.plist" "$LAUNCH_AGENTS_DIR/"
        print_status "已复制 $service.plist"
    done
    
    print_status "服务安装完成！"
}

# 启动服务
start_services() {
    print_status "启动 CaseLedger 服务..."
    
    for service in "${SERVICES[@]}"; do
        launchctl load "$LAUNCH_AGENTS_DIR/$service.plist"
        if [ $? -eq 0 ]; then
            print_status "$service 启动成功"
        else
            print_error "$service 启动失败"
        fi
    done
}

# 停止服务
stop_services() {
    print_status "停止 CaseLedger 服务..."
    
    for service in "${SERVICES[@]}"; do
        launchctl unload "$LAUNCH_AGENTS_DIR/$service.plist" 2>/dev/null
        if [ $? -eq 0 ]; then
            print_status "$service 停止成功"
        else
            print_warning "$service 可能未运行"
        fi
    done
}

# 重启服务
restart_services() {
    print_status "重启 CaseLedger 服务..."
    stop_services
    sleep 2
    start_services
}

# 查看服务状态
status_services() {
    print_status "CaseLedger 服务状态:"
    echo
    
    for service in "${SERVICES[@]}"; do
        status=$(launchctl list | grep $service)
        if [ -n "$status" ]; then
            echo -e "${GREEN}✓${NC} $service: 运行中"
            echo "  $status"
        else
            echo -e "${RED}✗${NC} $service: 未运行"
        fi
        echo
    done
}

# 查看日志
view_logs() {
    echo "选择要查看的日志:"
    echo "1) Flask 应用日志"
    echo "2) Celery Worker 日志"
    echo "3) Celery 全量同步 Worker 日志"
    echo "4) Celery Beat 日志"
    echo "5) 前端应用日志"
    echo "6) 错误日志"
    read -p "请输入选择 (1-6): " choice
    
    case $choice in
        1)
            tail -f "$PROJECT_DIR/logs/app.log"
            ;;
        2)
            tail -f "$PROJECT_DIR/logs/worker.log"
            ;;
        3)
            tail -f "$PROJECT_DIR/logs/full-worker.log"
            ;;
        4)
            tail -f "$PROJECT_DIR/logs/beat.log"
            ;;
        5)
            tail -f "$PROJECT_DIR/logs/frontend.log"
            ;;
        6)
            echo "错误日志:"
            echo "=== Flask 应用错误 ==="
            tail -20 "$PROJECT_DIR/logs/app.error.log" 2>/dev/null || echo "无错误日志"
            echo "=== Worker 错误 ==="
            tail -20 "$PROJECT_DIR/logs/worker.error.log" 2>/dev/null || echo "无错误日志"
            echo "=== Full Worker 错误 ==="
            tail -20 "$PROJECT_DIR/logs/full-worker.error.log" 2>/dev/null || echo "无错误日志"
            echo "=== Beat 错误 ==="
            tail -20 "$PROJECT_DIR/logs/beat.error.log" 2>/dev/null || echo "无错误日志"
            echo "=== 前端应用错误 ==="
            tail -20 "$PROJECT_DIR/logs/frontend.error.log" 2>/dev/null || echo "无错误日志"
            ;;
        *)
            print_error "无效选择"
            ;;
    esac
}

# 卸载服务
uninstall_services() {
    print_warning "这将完全卸载 CaseLedger 服务"
    read -p "确认继续? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        stop_services
        
        for service in "${SERVICES[@]}"; do
            rm -f "$LAUNCH_AGENTS_DIR/$service.plist"
            print_status "已删除 $service.plist"
        done
        
        print_status "服务卸载完成"
    else
        print_status "取消卸载"
    fi
}

# 主菜单
show_menu() {
    echo
    echo "=== CaseLedger 服务管理 ==="
    echo "1) 安装服务"
    echo "2) 启动服务"
    echo "3) 停止服务"
    echo "4) 重启服务"
    echo "5) 查看状态"
    echo "6) 查看日志"
    echo "7) 卸载服务"
    echo "8) 退出"
    echo
}

# 主程序
if [ $# -eq 0 ]; then
    # 交互模式
    while true; do
        show_menu
        read -p "请选择操作 (1-8): " choice
        
        case $choice in
            1) install_services ;;
            2) start_services ;;
            3) stop_services ;;
            4) restart_services ;;
            5) status_services ;;
            6) view_logs ;;
            7) uninstall_services ;;
            8) exit 0 ;;
            *) print_error "无效选择，请重试" ;;
        esac
        
        echo
        read -p "按回车键继续..."
    done
else
    # 命令行模式
    case $1 in
        install) install_services ;;
        start) start_services ;;
        stop) stop_services ;;
        restart) restart_services ;;
        status) status_services ;;
        logs) view_logs ;;
        uninstall) uninstall_services ;;
        *) 
            echo "用法: $0 [install|start|stop|restart|status|logs|uninstall]"
            echo "或者直接运行 $0 进入交互模式"
            exit 1
            ;;
    esac
fi
