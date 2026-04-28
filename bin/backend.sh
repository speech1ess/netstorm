#!/bin/bash

# Настройки
WORKERS=24
BASE_PORT=8100
SCRIPT="/opt/pmi/lib/system_monitor.py"
LOG_DIR="/dev/null"
NETNS="webserver"  # <--- Имя неймспейса

# Цвета
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

case "$1" in
    start)
        echo -e "${GREEN}Starting workers inside netns '${NETNS}'...${NC}"
        
        # 0. Проверяем, поднят ли loopback внутри неймспейса (важно!)
        ip netns exec $NETNS ip link set lo up
        
        for i in $(seq 1 $WORKERS); do
            port=$((BASE_PORT + i))
            # Проверяем порт ВНУТРИ неймспейса
            if ip netns exec $NETNS lsof -Pi :$port -sTCP:LISTEN -t >/dev/null ; then
                echo "Port $port inside $NETNS is busy. Skipping."
            else
                # === ГЛАВНАЯ МАГИЯ ===
                # Запускаем питон внутри неймспейса
                nohup ip netns exec $NETNS python3 $SCRIPT $port > $LOG_DIR 2>&1 &
            fi
        done
        echo -e "${GREEN}Done. Python farm is running inside $NETNS.${NC}"
        ;;

    stop)
        echo -e "${RED}Stopping workers...${NC}"
        # Убиваем всех питонов (pkill видит процессы из всех неймспейсов, так что сработает)
        pkill -f "python3 $SCRIPT"
        echo -e "${GREEN}All workers stopped.${NC}"
        ;;

    status)
        # Считаем процессы
        count=$(pgrep -f "python3 $SCRIPT" | wc -l)
        if [ "$count" -gt 0 ]; then
            echo -e "${GREEN}Running workers: $count${NC}"
            # Проверяем, видны ли порты внутри неймспейса
            echo "Checking ports inside '$NETNS':"
            ip netns exec $NETNS netstat -tulpn | grep python | head -n 5
        else
            echo -e "${RED}No workers running.${NC}"
        fi
        ;;

    *)
        echo "Usage: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac        