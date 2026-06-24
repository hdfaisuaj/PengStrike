#!/usr/bin/env bash
#
# PengStrike 一键安装脚本（无知识库版本）
# 支持: Kali Linux / Ubuntu / Debian
#

set -euo pipefail

# ========================================================================
# 颜色 & 工具定义
# ========================================================================
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
log_ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }
log_title() { echo -e "${MAGENTA}$1${NC}"; }
log_step()  { echo -e "${BLUE}\n[STEP $1/$TOTAL_STEPS]${NC} $2"; }

show_progress() {
    local percent=$1
    local width=50
    local filled=$((percent * width / 100))
    local empty=$((width - filled))
    printf "\r["
    printf "%0.s#" $(seq 1 $filled 2>/dev/null || true)
    printf "%0.s-" $(seq 1 $empty 2>/dev/null || true)
    printf "] %3d%%" $percent
}

TOTAL_STEPS=10
CURRENT_STEP=0

# ========================================================================
# 菜单: 镜像源选择
# ========================================================================
select_mirror() {
    echo ""
    log_title "════════════════════════════════════════════════════════"
    log_title "         第1步：请选择软件源（国内用户推荐清华源）"
    log_title "════════════════════════════════════════════════════════"
    echo ""

    echo -e "${GREEN}【选项 1】清华镜像源（国内推荐）${NC}"
    echo "   ✅ pip: https://pypi.tuna.tsinghua.edu.cn/simple"
    echo "   ✅ npm: https://registry.npmmirror.com"
    echo "   ✅ 速度快，稳定，适合国内网络"
    echo ""

    echo -e "${CYAN}【选项 2】官方源（默认）${NC}"
    echo "   ✅ 适合海外服务器、网络好的环境"
    echo ""

    log_title "════════════════════════════════════════════════════════"
    echo ""

    while true; do
        read -p "请输入你的选择 [1/2]（默认 1）: " choice
        choice=${choice:-1}

        case $choice in
            1)
                MIRROR="china"
                PIP_MIRROR="-i https://pypi.tuna.tsinghua.edu.cn/simple"
                NPM_MIRROR="--registry https://registry.npmmirror.com"
                log_ok "已选择：清华镜像源（国内加速）"
                break
                ;;
            2)
                MIRROR="official"
                PIP_MIRROR=""
                NPM_MIRROR=""
                log_ok "已选择：官方源"
                break
                ;;
            *) log_error "无效选择，请输入 1 或 2" ;;
        esac
    done
}

# ========================================================================
# 自动备份
# ========================================================================
backup_old_config() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "自动备份旧配置"
    show_progress 5

    BACKUP_DIR="backup_$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$BACKUP_DIR"

    [[ -f "config.json" ]] && cp config.json "$BACKUP_DIR/"
    [[ -f "pentest.db" ]] && cp pentest.db "$BACKUP_DIR/"
    for f in Pentest*.sh; do [[ -f "$f" ]] && cp "$f" "$BACKUP_DIR/"; done

    log_ok "备份完成: $BACKUP_DIR/"
    show_progress 10
}

# ========================================================================
# 环境检查 & 依赖安装
# ========================================================================
check_environment() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "检查系统环境"
    show_progress 15

    PYTHON="python3"
    PY_VERSION=$($PYTHON --version 2>&1 | awk '{print $2}')
    log_ok "Python $PY_VERSION"
    show_progress 20
}

install_system_deps() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "安装系统依赖"
    show_progress 25
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3-pip python3-venv python3-setuptools python3-wheel git wkhtmltopdf curl wget dos2unix || true
    log_ok "系统依赖完成"
    show_progress 30
}

install_pentest_tools() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "检测渗透工具（Kali自带自动跳过）"
    show_progress 35

    local TOOLS=(nmap masscan whois dig sqlmap hydra gobuster ffuf nikto searchsploit theharvester sublist3r dnsrecon amass)
    local installed=0
    for tool in "${TOOLS[@]}"; do
        command -v "$tool" &>/dev/null && ((installed++)) || sudo apt-get install -y -qq "$tool" 2>/dev/null || true
    done

    log_ok "渗透工具: 已安装 $installed / ${#TOOLS[@]}"
    show_progress 40
}

install_nodejs() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "安装 Node.js（实时显示进度）"
    show_progress 45

    if ! command -v npm &>/dev/null; then
        log_info "添加 NodeSource 源..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        log_info "安装 Node.js（下载进度会实时显示）..."
        sudo apt-get install -y nodejs 2>&1 | while IFS= read -r line; do
            if [[ "$line" =~ [0-9]+% ]] || [[ "$line" =~ "Unpacking" ]] || [[ "$line" =~ "Setting up" ]]; then
                echo -e "  ${CYAN}${line}${NC}"
            fi
        done
    fi
    log_ok "Node.js: $(node --version)"
    show_progress 50
}

setup_python_env() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "配置 Python 虚拟环境"
    show_progress 55

    [[ ! -d "venv" ]] && $PYTHON -m venv venv && log_ok "虚拟环境创建成功"
    source venv/bin/activate
    pip install --upgrade pip $PIP_MIRROR

    show_progress 60
    log_info "安装 Python 依赖 (过程将实时显示)..."
    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    pip install -r requirements.txt $PIP_MIRROR

    echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

    log_ok "Python 依赖安装完成"
    show_progress 70
}

# 创建项目运行目录结构
init_project_dirs() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "初始化项目目录结构"

    mkdir -p logs
    touch logs/.gitkeep
    log_ok "日志目录: logs/"

    show_progress 73
}

install_frontend() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "安装前端依赖（实时显示进度）"
    show_progress 75

    cd frontend
    if [[ ! -d "node_modules" ]]; then
        log_info "npm 安装依赖中（请等待，进度实时显示）..."
        npm install --progress $NPM_MIRROR
        log_ok "前端依赖安装完成"
    else
        log_ok "前端依赖已存在"
    fi

    # 确保 lodash-es 已安装（Settings.vue 的防抖保存依赖）
    if [[ ! -d "node_modules/lodash-es" ]]; then
        log_info "补充安装 lodash-es..."
        npm install lodash-es --save $NPM_MIRROR
    fi

    cd ..

    show_progress 80
}

# ========================================================================
# 生成启动脚本
# ========================================================================
generate_start_scripts() {
    CURRENT_STEP=$((CURRENT_STEP + 1))
    log_step $CURRENT_STEP "生成一键启动脚本"
    show_progress 85

    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

    cat > Pentest-Web.sh << 'EOF'
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

EOF
    chmod +x Pentest-Web.sh

    log_ok "启动脚本生成完成"
    show_progress 90
}

setup_config_json() {
    if [[ ! -f "config.json" ]]; then
        if [[ -f "config.json.example" ]]; then
            cp config.json.example config.json
            log_ok "config.json 已从 config.json.example 复制"
        else
            cat > config.json << 'JSONEOF'
{
  "llm": {
    "base_url": "",
    "api_key": "",
    "model": "gpt-3.5-turbo",
    "provider": "openai",
    "temperature": 0.1,
    "max_tokens": null,
    "azure_api_version": "2024-02-15-preview"
  },
  "system": {
    "auto_pilot_max_steps": 20,
    "command_timeout": 300,
    "log_level": "INFO",
    "dangerous_interrupt": true,
    "context_max_tokens": 8192,
    "context_reserve_recent": 4,
    "cors_allowed_origins": "*"
  },
  "mcp": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 8911
  },
  "report": {
    "output_dir": null
  },
  "database": {
    "url": null
  },
  "backend": {
    "host": "127.0.0.1",
    "port": 8000,
    "frontend_port": 5173
  }
}
JSONEOF
            log_ok "config.json 已生成"
        fi
        log_warn "⚠️  首次使用请在前端设置页面配置 LLM API"
    else
        log_info "config.json 已存在，跳过生成"
    fi

    # 自动检测本机 IP 并更新 backend 段
    log_info "检测本机 IP 地址..."
    KALI_IP=$(ip -4 addr show 2>/dev/null | awk '/192\.168\./ {print $2; exit}' | cut -d'/' -f1)
    if [ -z "$KALI_IP" ]; then
        KALI_IP=$(ip -4 addr show 2>/dev/null | awk '/inet / && $2 !~ /^127\./ {print $2; exit}' | cut -d'/' -f1)
    fi
    if [ -z "$KALI_IP" ]; then
        KALI_IP="127.0.0.1"
    fi

    python3 -c "
import json, os
config_file = 'config.json'
with open(config_file, 'r', encoding='utf-8') as f:
    config = json.load(f)
config.setdefault('backend', {})
config['backend']['host'] = '$KALI_IP'
config['backend']['port'] = 8000
config['backend']['frontend_port'] = 5173
with open(config_file, 'w', encoding='utf-8') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)
print('✅ backend 配置已更新: host=$KALI_IP')
" 2>/dev/null || true

    log_ok "config.json 配置就绪（后端地址: $KALI_IP:8000）"
}

# ========================================================================
# 完成
# ========================================================================
print_finish() {
    show_progress 100
    echo ""
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo -e "║              ${GREEN}🎉 PengStrike 安装完成！${NC}             ║"
    echo "╚════════════════════════════════════════════════════════════╝"
    echo ""
    echo " 启动方式："
    [[ -f "Pentest-Web.sh" ]] && echo -e "   ${BLUE}./Pentest-Web.sh${NC}   — 一键启动 Web 界面"
    echo ""
    echo "⚙️  首次使用："
    echo "   1. 运行 ./Pentest-Web.sh 启动服务"
    echo "   2. 浏览器访问 http://127.0.0.1:5173"
    echo "   3. 进入【设置】页面，配置 LLM API"
    echo "      - 填写 API 地址、API Key、模型名称"
    echo "      - 点击【添加】保存配置"
    echo ""
    echo "📖 详细说明请查看：README.md"
    echo ""
}

check_already_installed() {
    if [[ -d "venv" ]] && [[ -f "venv/bin/python" ]]; then
        echo ""
        log_title "════════════════════════════════════════════════════════"
        log_title "         📦 检测到已安装的 PengStrike"
        log_title "════════════════════════════════════════════════════════"
        echo ""

        if source venv/bin/activate && python -c "import fastapi, uvicorn, sqlalchemy" 2>/dev/null; then
            echo -e "${GREEN}✅ 检测到完整安装，核心依赖正常！${NC}"
            echo ""
            echo -e "${CYAN}检测结果：${NC}"
            echo "   • 虚拟环境: 已存在"
            echo "   • Python依赖: 已安装"
            echo "   • 核心模块: fastapi, uvicorn, sqlalchemy ✓"
            echo ""

            while true; do
                read -p "是否跳过重装，直接生成启动脚本？ [Y/n] " skip
                skip=${skip:-Y}
                case $skip in
                    [Yy]*)
                        echo ""
                        log_ok "✅ 跳过安装，直接生成启动脚本..."
                        echo ""

                        cd "$(dirname "$0")"
                        BACKUP_DIR="跳过安装"
                        select_mirror
                        generate_start_scripts
                        setup_config_json
                        print_finish
                        exit 0
                        ;;
                    [Nn]*)
                        log_info "将执行完整重新安装..."
                        break
                        ;;
                    *) log_error "无效选择，请输入 Y 或 N" ;;
                esac
            done
        fi
    fi
}

main() {
    clear
    echo ""
    echo "╔══════════════════════════════════════════════════════════╗"
    echo "║           PengStrike 智能安装向导                 ║"
    echo "╚════════════════════════════════════════════════════════════╝"

    cd "$(dirname "$0")"

    check_already_installed

    select_mirror

    echo ""
    log_info "开始安装..."
    echo ""

    backup_old_config
    check_environment
    install_system_deps
    install_pentest_tools
    install_nodejs
    setup_python_env
    init_project_dirs
    install_frontend
    generate_start_scripts
    setup_config_json

    print_finish
}

main "$@"
