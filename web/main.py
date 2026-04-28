"""
NetStorm Orchestrator Web Server (FastAPI).
Главная точка входа для запуска UI и REST API.
"""
import sys
import os
import logging
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# 🟢 1. ДИНАМИЧЕСКИЕ ПУТИ (Убиваем хардкод /opt/pmi)
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__)) # Папка web/
BASE_DIR = os.path.dirname(CURRENT_DIR)                  # Корень проекта (pmi/)
LIB_DIR = os.path.join(BASE_DIR, 'lib')

if LIB_DIR not in sys.path:
    sys.path.append(LIB_DIR)

# Теперь безопасно импортируем SharedConfig
from shared import SharedConfig

# 🟢 2. НАСТРОЙКА СИСТЕМНОГО ЛОГГЕРА (Пишем в pmi_web.log)
LOG_BASE_DIR = SharedConfig.get('paths.logs', os.path.join(BASE_DIR, 'logs'))
os.makedirs(LOG_BASE_DIR, exist_ok=True)
web_log_path = os.path.join(LOG_BASE_DIR, "pmi_web.log")

import shutil
import datetime
if os.path.exists(web_log_path):
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(LOG_BASE_DIR, f"pmi_web_{timestamp}.bak")
    try:
        shutil.move(web_log_path, backup_path) # Переносим старый лог в бэкап
    except Exception:
        pass # Если файл заблокирован, просто едем дальше

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [WEB] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler(web_log_path, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("pmi_web")
logger.info("🟢 NetStorm API Server starting up...")

# 🟢 3. ИМПОРТЫ РОУТЕРОВ
from routers.status_router import router as status_api
from routers.config_router import router as config_api
from routers.exec_router import router as exec_api

# 🟢 4. ИНИЦИАЛИЗАЦИЯ ПРИЛОЖЕНИЯ
app = FastAPI(title="NetStorm Orchestrator")

# Монтируем статику через динамический путь
STATIC_DIR = os.path.join(CURRENT_DIR, 'static')
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Подключаем модули к основному приложению
app.include_router(status_api)
app.include_router(config_api)
app.include_router(exec_api)

@app.get("/", response_class=HTMLResponse)
async def index():
    """
    Отдает главную страницу SPA (Single Page Application).
    Читает index.html по динамическому пути.
    """
    template_path = os.path.join(CURRENT_DIR, 'templates', 'index.html')
    
    if os.path.exists(template_path):
        with open(template_path, 'r', encoding='utf-8') as f:
            return f.read()
            
    logger.error(f"Template not found at: {template_path}")
    return f"<h1>Error: template index.html not found!</h1><p>Checked path: {template_path}</p>"