#!/usr/bin/env bash
set -euo pipefail

# ======== 0. 初始化 ========
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONIOENCODING=utf-8
VENV_DIR="$SCRIPT_DIR/.venv"
PY_CMD=""

echo "[MeaPet] 工作目录: $SCRIPT_DIR"

# ======== 1. 检测已有 Python ========
find_python() {
    # 1a. 检查项目内 venv
    if [[ -x "$VENV_DIR/bin/python3" ]]; then
        PY_CMD="$VENV_DIR/bin/python3"
        return 0
    fi

    # 1b. 检查 Hermes venv (Linux 常见路径)
    local hermes_paths=(
        "$HOME/.local/share/hermes/hermes-agent/venv/bin/python3"
        "$HOME/.hermes/hermes-agent/venv/bin/python3"
    )
    for hp in "${hermes_paths[@]}"; do
        if [[ -x "$hp" ]]; then
            PY_CMD="$hp"
            return 0
        fi
    done

    # 1c. 系统 PATH 中的 python3 / python
    if command -v python3 &>/dev/null; then
        PY_CMD="python3"
        return 0
    elif command -v python &>/dev/null; then
        PY_CMD="python"
        return 0
    fi

    return 1
}

if find_python; then
    echo "[MeaPet] 检测到 Python: $PY_CMD ($($PY_CMD --version 2>&1))"
else
    echo "[MeaPet] ❌ 未检测到 Python3！"
    echo ""
    echo "请通过系统包管理器安装 Python 3.10+："
    echo "  Debian/Ubuntu: sudo apt install python3 python3-pip python3-venv"
    echo "  Fedora/RHEL:   sudo dnf install python3 python3-pip"
    echo "  Arch Linux:    sudo pacman -S python python-pip"
    echo "  macOS:         brew install python3"
    echo ""
    echo "安装后请重新运行此脚本。"
    exit 1
fi

# ======== 2. 创建/激活虚拟环境 ========
if [[ ! -d "$VENV_DIR" ]]; then
    echo "[MeaPet] 正在创建虚拟环境 ..."
    $PY_CMD -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
PY_CMD="$VENV_DIR/bin/python3"

echo "[MeaPet] 使用虚拟环境: $VENV_DIR"
echo "[MeaPet] Python 版本: $($PY_CMD --version 2>&1)"

# ======== 3. 确保 pip 可用 ========
if ! $PY_CMD -m pip --version &>/dev/null; then
    echo "[MeaPet] 正在安装 pip ..."
    $PY_CMD -m ensurepip --upgrade 2>/dev/null || {
        echo "[MeaPet] ensurepip 失败，尝试 get-pip.py ..."
        curl -sSL https://bootstrap.pypa.io/get-pip.py | $PY_CMD
    }
fi

# ======== 4. 安装基础依赖 ========
REQ_FILE="$SCRIPT_DIR/linux_requirements.txt"
if [[ ! -f "$REQ_FILE" ]]; then
    echo "[MeaPet] ⚠️  未找到 linux_requirements.txt，尝试使用 requirements.txt ..."
    REQ_FILE="$SCRIPT_DIR/requirements.txt"
fi

if [[ -f "$REQ_FILE" ]]; then
    echo "[MeaPet] 正在安装基础依赖 ..."
    echo "[MeaPet] 💡 Live2D 模型支持需手动配置，下载地址及说明请参阅项目 README"
    $PY_CMD -m pip install -r "$REQ_FILE" \
        --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
        --trusted-host pypi.tuna.tsinghua.edu.cn \
        -q || {
        echo "[MeaPet] ❌ 基础依赖安装失败"
        exit 1
    }
else
    echo "[MeaPet] ⚠️  未找到任何 requirements 文件，跳过依赖安装"
fi

# ======== 5. 启动 ========
if [[ ! -f "$SCRIPT_DIR/config.json" ]]; then
    echo "[MeaPet] 首次运行，启动配置向导 ..."
    $