#!/bin/bash
# Pentest-Web.sh — 一键启动脚本（Ctrl+C 停止所有服务）
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
source venv/bin/activate

# ═══════════════════════════════════════════════════════
# 健康检查函数
# ═══════════════════════════════════════════════════════
health_check() {
    echo ""
    echo "🔍 正在进行健康检查..."
    for i in $(seq 1 30); do
        curl -s http://127.0.0.1:8000/api/health >/dev/null 2>&1 && \
            echo "   ✅ 后端服务正常" && break
        sleep 1
        printf "."
    done
    echo ""
    echo "✅ 健康检查完成！"
    echo ""
}

# ═══════════════════════════════════════════════════════
# 停止所有服务（trap 处理函数）
# ═══════════════════════════════════════════════════════
shutdown_all() {
    trap '' INT TERM EXIT

    echo ""
    echo "🛑 正在停止所有服务..."

    for pidfile in /tmp/pentest_backend.pid /tmp/pentest_frontend.pid; do
        if [ -f "$pidfile" ]; then
            pid=$(cat "$pidfile" 2>/dev/null)
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill -TERM "$pid" 2>/dev/null || true
                sleep 1
                kill -TERM -"$pid" 2>/dev/null || true
                sleep 2
                kill -KILL "$pid" 2>/dev/null || true
                kill -KILL -"$pid" 2>/dev/null || true
            fi
            rm -f "$pidfile"
        fi
    done

    pkill -TERM -f "uvicorn api.app" 2>/dev/null || true
    pkill -TERM -f "npm run dev" 2>/dev/null || true
    pkill -TERM -f "node.*vite" 2>/dev/null || true
    sleep 2
    pkill -KILL -f "uvicorn api.app" 2>/dev/null || true
    pkill -KILL -f "npm run dev" 2>/dev/null || true
    pkill -KILL -f "node.*vite" 2>/dev/null || true

    fuser -k 8000/tcp 2>/dev/null || true
    fuser -k 5173/tcp 2>/dev/null || true
    fuser -k 5174/tcp 2>/dev/null || true

    echo "✅ 服务已完全停止"
    exit 0
}

trap shutdown_all INT TERM HUP EXIT

echo "========================================"
echo "  PengStrike - Web 界面"
echo "========================================"
echo "后端: http://127.0.0.1:8000"
echo "前端: http://127.0.0.1:5173"
echo "按 Ctrl+C 停止所有服务"
echo ""

# ═══════════════════════════════════════════════════════
# HF 镜像加速（国内网络环境）
# ═══════════════════════════════════════════════════════
export HF_ENDPOINT="https://hf-mirror.com"
export HF_HUB_DISABLE_TELEMETRY=1
export ANONYMIZED_TELEMETRY=False
echo "🐑 HF 镜像: $HF_ENDPOINT"

mkdir -p "$SCRIPT_DIR/logs"

nohup "$SCRIPT_DIR/venv/bin/uvicorn" api.app:app \
    --host 0.0.0.0 \
    --port 8000 \
    > logs/backend.log 2>&1 &
BACKEND_PID=$!
echo $BACKEND_PID > /tmp/pentest_backend.pid

cd "$SCRIPT_DIR/frontend"
nohup npm run dev > /dev/null 2>&1 &
FRONTEND_PID=$!
echo $FRONTEND_PID > /tmp/pentest_frontend.pid
cd "$SCRIPT_DIR"

health_check

echo "🟢 服务运行中...（按 Ctrl+C 停止）"
wait

