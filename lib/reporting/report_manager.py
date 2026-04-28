#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import time
import subprocess
import shutil
from datetime import datetime

# 🟢 Подключаем наши общие инструменты из ядра!
from shared import SharedConfig
from pmi_logger import Log

# Берем пути из глобального конфига (с фоллбэком на дефолты)
LOGS_DIR = SharedConfig.get('paths.logs', '/opt/pmi/logs')
RESULTS_DIR = SharedConfig.get('paths.results', '/opt/pmi/results')

CRON_MARKER = "# PMI_AUTO_ROTATION"
PYTHON_BIN = sys.executable or "python3"

# Динамически формируем пути к скриптам-соседям внутри пакета reporting
REPORTING_DIR = os.path.dirname(os.path.abspath(__file__))
SUMMARY_SCRIPT = os.path.join(REPORTING_DIR, 'generate_run_summary.py')
INDEX_SCRIPT = os.path.join(REPORTING_DIR, 'generate_index.py')


# ════════════════════════════════════════════════════════
# 1. ГЕНЕРАТОР МЕНЮ СЕССИЙ (ДЛЯ ВЫБОРОЧНОЙ ПЕРЕСБОРКИ)
# ════════════════════════════════════════════════════════
def get_session_regen_menu():
    """Возвращает список последних 20 сессий для меню"""
    nodes = []
    if not os.path.exists(LOGS_DIR):
        return nodes
    
    sessions = [d for d in os.listdir(LOGS_DIR) if os.path.isdir(os.path.join(LOGS_DIR, d)) and d.startswith('20')]
    sessions.sort(reverse=True)
    
    for sess in sessions[:20]:
        nodes.append({
            "type": "command",
            "label": f"► Rebuild: {sess}",
            "cmd": f"{PYTHON_BIN} {SUMMARY_SCRIPT} {sess}"
        })
    return nodes

# ════════════════════════════════════════════════════════
# 2. ПЕРЕСБОРКА ОТЧЕТОВ
# ════════════════════════════════════════════════════════
def rebuild_all():
    """Пересобирает HTML отчеты для ВСЕХ найденных сессий"""
    Log.warning("Starting FULL rebuild of all HTML reports. This might take a while...")
    if not os.path.exists(LOGS_DIR):
        Log.error("Logs directory not found.")
        return

    sessions = [d for d in os.listdir(LOGS_DIR) if os.path.isdir(os.path.join(LOGS_DIR, d)) and d.startswith('20')]
    sessions.sort()
    
    for sess in sessions:
        Log.info(f"Rebuilding session: {sess}")
        subprocess.run([PYTHON_BIN, SUMMARY_SCRIPT, sess], stdout=subprocess.DEVNULL)
    
    Log.info("Regenerating main dashboard index...")
    subprocess.run([PYTHON_BIN, INDEX_SCRIPT, "--generate"])
    Log.success(f"Successfully rebuilt {len(sessions)} reports!")

# ════════════════════════════════════════════════════════
# 3. ОЧИСТКА ХРАНИЛИЩА (РОТАЦИЯ)
# ════════════════════════════════════════════════════════
def clean_raw_logs_7d():
    """Удаляет тяжелые логи (.jtl, .log), старше 7 дней, оставляя HTML"""
    Log.info("Starting cleanup of raw logs older than 7 days...")
    cutoff = time.time() - (7 * 86400)
    cleaned_bytes = 0
    
    targets = [LOGS_DIR, RESULTS_DIR]
    exts_to_kill = ('.jtl', '.log', '.csv', '.pcap')
    
    for t_dir in targets:
        if not os.path.exists(t_dir): continue
        for root, dirs, files in os.walk(t_dir):
            for file in files:
                if file.endswith(exts_to_kill):
                    fpath = os.path.join(root, file)
                    try:
                        if os.path.getmtime(fpath) < cutoff:
                            size = os.path.getsize(fpath)
                            os.remove(fpath)
                            cleaned_bytes += size
                            Log.debug(f"Deleted: {file} ({size // 1024 // 1024} MB)")
                    except Exception as e:
                        Log.error(f"Failed to delete {file}: {e}")
                        
    Log.success(f"Cleanup finished. Freed {cleaned_bytes // 1024 // 1024} MB of disk space.")

def clean_all_30d():
    """Полностью удаляет папки сессий (включая HTML) старше 30 дней"""
    Log.warning("Starting FULL PURGE of sessions older than 30 days...")
    cutoff = time.time() - (30 * 86400)
    deleted_sessions = 0
    
    targets = [LOGS_DIR, RESULTS_DIR]
    for t_dir in targets:
        if not os.path.exists(t_dir): continue
        
        folders = [d for d in os.listdir(t_dir) if os.path.isdir(os.path.join(t_dir, d)) and d.startswith('20')]
        for folder in folders:
            fpath = os.path.join(t_dir, folder)
            try:
                if os.path.getmtime(fpath) < cutoff:
                    shutil.rmtree(fpath)
                    deleted_sessions += 1
                    Log.debug(f"Purged old session folder: {folder}")
            except Exception as e:
                Log.error(f"Failed to purge folder {folder}: {e}")
                
    Log.info("Regenerating main dashboard index...")
    subprocess.run([PYTHON_BIN, INDEX_SCRIPT, "--generate"])
    Log.success(f"Purge finished. Deleted {deleted_sessions} old sessions.")

# ════════════════════════════════════════════════════════
# 4. УПРАВЛЕНИЕ CRON (АВТОМАТИЗАЦИЯ)
# ════════════════════════════════════════════════════════
def enable_cron_rotation():
    """Добавляет задачу в cron на выполнение очистки каждую ночь в 03:00"""
    Log.info("Setting up auto-rotation in cron...")
    script_path = os.path.abspath(__file__)
    
    cron_job = f"0 3 * * * . /opt/pmi/.env && {PYTHON_BIN} {script_path} --auto-clean {CRON_MARKER}"
    
    try:
        current_cron = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode('utf-8')
    except subprocess.CalledProcessError:
        current_cron = ""
        
    if CRON_MARKER in current_cron:
        Log.warning("Auto-rotation is already enabled.")
        return
        
    new_cron = current_cron.strip() + f"\n{cron_job}\n"
    
    p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    p.communicate(new_cron.encode('utf-8'))
    Log.success("Cron job added! Raw logs will be cleaned every night at 03:00 AM.")

def disable_cron_rotation():
    """Удаляет нашу задачу из cron"""
    Log.info("Removing auto-rotation from cron...")
    try:
        current_cron = subprocess.check_output(["crontab", "-l"], stderr=subprocess.DEVNULL).decode('utf-8')
    except subprocess.CalledProcessError:
        Log.warning("No crontab found.")
        return
        
    if CRON_MARKER not in current_cron:
        Log.warning("Auto-rotation is not currently enabled.")
        return
        
    new_cron = "\n".join([line for line in current_cron.split('\n') if CRON_MARKER not in line])
    
    p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    p.communicate(new_cron.encode('utf-8'))
    Log.success("Auto-rotation disabled successfully.")

# ════════════════════════════════════════════════════════
# ТОЧКА ВХОДА (Для запуска из Cron)
# ════════════════════════════════════════════════════════
if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--auto-clean":
        Log.info(f"[{datetime.now()}] Running auto-cleanup tasks...")
        clean_raw_logs_7d()
        clean_all_30d()