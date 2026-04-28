#!/bin/bash
# ═══════════════════════════════════════════════════════════
# PMI ORCHESTRATOR - MAIN LAUNCHER
# ═══════════════════════════════════════════════════════════

# Определяем пути
PMI_HOME="/opt/pmi"
LIB_DIR="$PMI_HOME/lib"
CONFIG_DIR="$PMI_HOME/config"
WEB_DIR="$PMI_HOME/web"

# Добавляем lib в путь поиска модулей питона
export PYTHONPATH="$LIB_DIR:$PYTHONPATH"

# Генерим RUN_ID для сессии оркестратора
if [ -z "$PMI_RUN_ID" ]; then
  export PMI_RUN_ID="$(date +%Y%m%d_%H%M%S)"
fi
export PMI_IS_ORCH=1

# Проверка Python
if ! command -v python3 &> /dev/null; then
    echo -e "\033[1;31mError: Python3 is not installed.\033[0m"
    exit 1
fi

# Проверка PyYAML (попытка авто-установки для MVP, если есть pip)
if ! python3 -c "import yaml" 2>/dev/null; then
    echo -e "\033[1;33m[!] PyYAML not found. Trying to install...\033[0m"
    if command -v pip3 &> /dev/null; then
        pip3 install pyyaml --user
    elif command -v apt-get &> /dev/null; then
        sudo apt-get install python3-yaml -y
    else
        echo "Error: Please install python3-yaml manually."
        exit 1
    fi
fi

# Функция вывода помощи
show_help() {
    echo -e "\033[1;36m=== PMI Orchestrator Launcher ===\033[0m"
    echo "Usage: ./pmi_start.sh [mode]"
    echo ""
    echo "Modes:"
    echo "  tui | -t      - Start Interactive Console Menu (Default)"
    echo "  web | -w      - Start Web Dashboard (Foreground debug)"
    echo "  service | -s  - Manage Systemd Web Service (start/stop/status)"
    echo "  help | -h     - Show this message"
    echo ""
}

# Режим по умолчанию - TUI
MODE=${1:-tui}

case "$MODE" in
    tui|--tui|-t)
        echo -e "\033[1;32m[PMI] RUN_ID=${PMI_RUN_ID}\033[0m"
        echo -e "\033[1;32mStarting PMI Console (TUI)...\033[0m"
        python3 "$LIB_DIR/menu_builder.py"
        ;;
    web|--web|-w)
        export PMI_WEB_MODE=1
        echo -e "\033[1;32m[PMI] RUN_ID=${PMI_RUN_ID}\033[0m"
        echo -e "\033[1;32mStarting PMI Web Dashboard (Debug Mode)...\033[0m"
        cd "$WEB_DIR" || { echo -e "\033[1;31mDirectory $WEB_DIR not found!\033[0m"; exit 1; }
        uvicorn main:app --host 0.0.0.0 --port 8000 --reload
        ;;
    service|--service|-s)
        CMD=${2:-status}
        echo -e "\033[1;34mExecuting 'systemctl $CMD pmi-web'...\033[0m"
        sudo systemctl $CMD pmi-web
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo -e "\033[1;31mUnknown mode: $MODE\033[0m"
        show_help
        exit 1
        ;;
esac