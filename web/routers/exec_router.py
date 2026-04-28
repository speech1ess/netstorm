import subprocess
import shlex
import os
import sys
import json
import logging
from fastapi import APIRouter, BackgroundTasks, Request
from pydantic import BaseModel
from typing import List, Optional

# Настраиваем логгер для роутера
logger = logging.getLogger("pmi_web")

try:
    from shared import SharedConfig
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        from shared import SharedConfig
    except ImportError:
        SharedConfig = None

BASE_DIR = SharedConfig.get('paths.base', '/opt/pmi') if SharedConfig else '/opt/pmi'
SESSION_STATE_FILE = os.path.join(BASE_DIR, '.session_state.json')
LOG_BASE_DIR = SharedConfig.get('paths.logs', '/opt/pmi/logs')

class BatchPayload(BaseModel):
    scenarios: List[str]
    preset: Optional[str] = None
    interval: Optional[int] = 60

router = APIRouter(prefix="/api", tags=["Execution"])

@router.post("/kill")
async def kill_processes():
    logger.info("🛑 [KILL SWITCH] Received kill command from UI.")
    try:
        subprocess.call("pkill -f 'scenario_runner.py|jmeter_driver.py|trex_driver.py'", shell=True)
        
        if os.path.exists(SESSION_STATE_FILE):
            try: os.remove(SESSION_STATE_FILE)
            except: pass
            
        return {"status": "success", "output": "🛑 ВСЕ ТЕСТЫ ОСТАНОВЛЕНЫ!"}
    except Exception as e:
        logger.error(f"Kill switch failed: {e}")
        return {"status": "error", "message": str(e)}

@router.post("/execute")
async def execute_action(payload: Request):
    """
    Синхронный эксекьютор для сервисных команд (Рестарт, Очистка и т.д.).
    Возвращает stdout/stderr прямо во фронтенд.
    """
    item = await payload.json()
    action_label = item.get('label', 'Unknown Action')
    logger.info(f"🚀 EXECUTING Action: {action_label}")
    
    output_text = ""
    status = "success"

    try:
        if item['type'] == 'command':
            result = subprocess.run(item['cmd'], shell=True, capture_output=True, text=True)
            output_text = result.stdout + result.stderr
            if result.returncode != 0:
                status = "error"
        
        elif item['type'] == 'python':
            mod = item['module']
            func = item['function']
            args_list = item.get('args', [])
            
            if custom_args := item.get('custom_args'):
                args_list.append(custom_args)
            
            args_str = ", ".join([f"'{str(a)}'" for a in args_list])
            py_cmd = f"import sys; sys.path.append('/opt/pmi/lib'); import {mod}; {mod}.{func}({args_str})"
            
            result = subprocess.run(["python3", "-c", py_cmd], capture_output=True, text=True)
            output_text = result.stdout + result.stderr
            if result.returncode != 0:
                status = "error"
                
    except Exception as e:
        status = "error"
        output_text = str(e)
        logger.error(f"Action {action_label} failed: {e}")
        
    # 🟢 Логируем не только статус, но и сам результат (если он есть)
    log_msg = f"Action {action_label} finished. Status: {status}"
    if output_text.strip():
        # Добавляем перенос строки, чтобы длинный вывод читался красиво
        log_msg += f"\n--- OUTPUT ---\n{output_text.strip()}\n--------------"
    
    logger.info(log_msg)
    
    # 🟢 Отдаем текст прямо в JS
    return {"status": status, "output": output_text.strip()}

@router.post("/execute/batch")
async def execute_batch(payload: BatchPayload, bt: BackgroundTasks):
    """Запуск пачки сценариев (Смарт Степперы). Раннер сам пишет логи в latest.log"""
    if not payload.scenarios:
        return {"status": "error", "message": "No scenarios provided"}

    cmd_parts = ["python3", "/opt/pmi/lib/runners/scenario_runner.py", "--api-batch"] 
    cmd_parts.extend(payload.scenarios)
    
    if payload.preset:
        cmd_parts.extend(["--preset", payload.preset])
    if payload.interval:
        cmd_parts.extend(["--interval", str(payload.interval)])

    cmd_str = " ".join(cmd_parts)
    logger.info(f"Starting Smart Batch: {cmd_str}")
    
    # Пускаем в бэкграунд
    bt.add_task(subprocess.call, cmd_str, shell=True)
    
    return {"status": "success", "output": f"Batch of {len(payload.scenarios)} scenarios started. Check live console."}

@router.post("/run/{sc_id}/{preset}")
async def run_attack(sc_id: str, preset: str, request: Request, bt: BackgroundTasks):
    """Одиночный запуск сценария."""
    p_val = None if preset == 'default' else preset
    cmd = ["python3", "/opt/pmi/lib/runners/scenario_runner.py", sc_id]
    if p_val: cmd.append(p_val)
    cmd.append("--batch") 
    
    try:
        body = await request.json()
        if custom_args := body.get('custom_args', ''):
            cmd.extend(shlex.split(custom_args))
    except Exception:
        pass

    logger.info(f"Starting Single Scenario: {' '.join(cmd)}")
    bt.add_task(subprocess.call, cmd)
    return {"status": "success", "output": f"Run initialized: {sc_id} [{preset}]. Check live console."}

@router.get("/session/state")
async def get_session_state():
    if os.path.exists(SESSION_STATE_FILE):
        try:
            with open(SESSION_STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {"status": "running"}

@router.post("/session/resume")
async def resume_session(request: Request):
    if os.path.exists(SESSION_STATE_FILE):
        try:
            # 🟢 Пытаемся безопасно прочитать JSON от фронтенда
            try:
                data = await request.json()
            except Exception:
                data = {}

            # 🟢 Пишем статус running и прокидываем опции (галочку IPS) в файл
            with open(SESSION_STATE_FILE, 'w') as f:
                json.dump({
                    "status": "running",
                    "options": data.get("options", {})
                }, f)
                
            logger.info("Session resumed by user.")
            return {"status": "success", "message": "Session resumed"}
        except Exception as e:
            return {"status": "error", "message": str(e)}
    return {"status": "error", "message": "No active session"}

@router.post("/logs/clear")
async def clear_logs():
    import shutil
    import datetime
    
    web_log = os.path.join(LOG_BASE_DIR, "pmi_web.log")
    if os.path.exists(web_log):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_log = os.path.join(LOG_BASE_DIR, f"pmi_web_{timestamp}.bak")
        shutil.copy2(web_log, backup_log)
        
        with open(web_log, 'w') as f:
            f.truncate(0)
            
        logger.info("Web log cleared and backed up via UI.")
        return {"status": "success", "output": f"Log backed up to pmi_web_{timestamp}.bak and cleared."}
    return {"status": "ignored", "output": "No web log found to clear."}