import os
import asyncio
import glob
from fastapi import APIRouter, Query # 🟢 Добавили Query сюда
from shared import SharedConfig
from monitoring.system_status import SystemStatus
from routers.config_router import strip_ansi
import scenario_loader

router = APIRouter(prefix="/api", tags=["Status & Logs"])
status_monitor = SystemStatus()

# Определяем пути ОДИН РАЗ на уровне модуля
LOG_BASE_DIR = SharedConfig.get('paths.logs', '/opt/pmi/logs')
LATEST_DIR = os.path.join(LOG_BASE_DIR, "latest")
LATEST_LOG = os.path.join(LOG_BASE_DIR, "latest.log")

@router.get("/status")
async def api_status():
    cmd = "pgrep -f '[s]cenario_runner.py|[j]meter_driver.py|[t]rex_driver.py'"
    process = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await process.communicate()
    
    return {
        "res": strip_ansi(status_monitor._get_resources_line()),
        "trace": strip_ansi(status_monitor._trace_visual()),
        "is_running": (process.returncode == 0),
        "active_config": scenario_loader._get_active_config()
    }

# 🟢 ПОЛНОСТЬЮ ОБНОВЛЕННЫЙ ЭНДПОИНТ
@router.get("/logs")
async def get_logs(type: str = Query("session")):
    """
    Умный эндпоинт. 
    type='session' -> читает боевой лог тестов
    type='web' -> читает системный лог оркестратора
    """
    if type == 'web':
        # Читаем системный лог
        target = os.path.join(LOG_BASE_DIR, "pmi_web.log")
        fallback_msg = "Ожидание системных событий..."
    else:
        # Читаем лог тестов
        target = LATEST_LOG
        fallback_log = os.path.join(LATEST_DIR, "pmi_session.log")
        
        # Если latest.log нет, но есть pmi_session (например, симлинк не успел создаться)
        if not os.path.exists(target) and os.path.exists(fallback_log):
            target = fallback_log
            
        fallback_msg = "Ожидание запуска сценария (симлинки не созданы)..."

    # Если целевой файл найден — читаем
    if target and os.path.exists(target):
        try:
            with open(target, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                return {"logs": strip_ansi("".join(lines[-500:]))}
        except Exception as e:
            return {"logs": f"Ошибка чтения лога: {e}"}
            
    # Если файла еще нет — отдаем заглушку
    return {"logs": fallback_msg}

@router.get("/diag/files")
async def list_diag_files():
    """Список файлов текущей сессии + глобальные логи"""
    try:
        files = []
        
        # 1. Берем стандартные логи из сессии
        if os.path.exists(LATEST_DIR):
            files = [os.path.basename(f) for f in glob.glob(f"{LATEST_DIR}/*.log")]
        
        # 2. Добавляем два твоих конкретных файла (если они физически существуют)
        extra_files = ["pmi_web.log", "pmi_system.log"]
        for ext_file in extra_files:
            if os.path.exists(os.path.join(LOG_BASE_DIR, ext_file)) and ext_file not in files:
                files.append(ext_file)
        
        # Сортируем: pmi_session.log -> системные логи -> все остальные
        def sort_key(x):
            if x == "pmi_session.log": return (0, x)
            if x in extra_files: return (1, x)
            return (2, x)
            
        files.sort(key=sort_key)
        
        return {"files": files}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@router.get("/diag/read/{filename}")
async def read_diag_file(filename: str):
    """Чтение конкретного файла с маршрутизацией путей"""
    try:
        safe_name = os.path.basename(filename)
        
        # ВАЖНО: Если это наши системные логи, ищем их в корне /opt/pmi/logs
        if safe_name in ["pmi_web.log", "pmi_system.log"]:
            file_path = os.path.join(LOG_BASE_DIR, safe_name)
        else:
            # Иначе ищем в папке сессии latest/
            file_path = os.path.join(LATEST_DIR, safe_name)
        
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = "".join(f.readlines()[-2000:])
                return {
                    "filename": safe_name,
                    "content": strip_ansi(content)
                }
        return {"content": f"Файл {safe_name} не найден"}
    except Exception as e:
        return {"content": f"Ошибка чтения: {e}"}