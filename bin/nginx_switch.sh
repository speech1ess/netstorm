#!/bin/bash

AVAILABLE="/etc/nginx/sites-available"
ENABLED="/etc/nginx/sites-enabled"
TARGET_LINK="$ENABLED/default" # Обычно дефолтный конфиг тут

# Цвета
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m'

case "$1" in
    static)
        echo -e "${BLUE}Switching to STATIC mode...${NC}"
        ln -sf "$AVAILABLE/loadtest" "$TARGET_LINK"
        ;;
    
    backend)
        echo -e "${BLUE}Switching to BACKEND (Python) mode...${NC}"
        ln -sf "$AVAILABLE/backend" "$TARGET_LINK"
        ;;
    
    status)
        CURRENT=$(readlink -f $TARGET_LINK)
        echo -e "Current mode: ${GREEN}$(basename $CURRENT)${NC}"
        exit 0
        ;;
    
    *)
        echo "Usage: $0 {static|backend|status}"
        exit 1
        ;;
esac

# Проверка и перезагрузка
echo "Testing Nginx config..."
nginx -t > /dev/null 2>&1

if [ $? -eq 0 ]; then
    nginx -s reload
    echo -e "${GREEN}Success! Nginx reloaded.${NC}"
    # Показать текущий режим
    CURRENT=$(readlink -f $TARGET_LINK)
    echo -e "Active Config: $(basename $CURRENT)"
else
    echo -e "${RED}Config ERROR! Nginx NOT reloaded.${NC}"
    nginx -t
fi
