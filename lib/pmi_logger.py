###/opt/pmi/lib/pmi_logger.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import logging
import os
import sys
import datetime
import re
from logging.handlers import RotatingFileHandler

try:
    from shared import SharedConfig, Colors
except ImportError:
    class SharedConfig:
        @staticmethod
        def get(k, d=None): return d
    class Colors:
        RED='\033[91m'; GREEN='\033[92m'; YELLOW='\033[93m'; ENDC='\033[0m'

ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# ─────────────────────────────────────────────────────────────
# FORMATTERS
# ─────────────────────────────────────────────────────────────
class PlainFileFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        return ANSI_RE.sub("", msg)

class ColorConsoleFormatter(logging.Formatter):
    def format(self, record):
        msg = super().format(record)
        if record.levelno >= logging.ERROR:
            prefix = f"{Colors.RED}[ERR]{Colors.ENDC}"
        elif record.levelno >= logging.WARNING:
            prefix = f"{Colors.YELLOW}[WARN]{Colors.ENDC}"
        elif record.levelno >= logging.INFO:
            if "[OK]" in msg:
                prefix = f"{Colors.GREEN}[OK]{Colors.ENDC}"
                msg = msg.replace("[OK]", "").strip()
            else:
                prefix = f"{Colors.GREEN}[INFO]{Colors.ENDC}"
        else:
            # Дебаг делаем серым, чтобы не рябило в глазах
            prefix = f"\033[90m[DBG]{Colors.ENDC}"
        return f"{prefix}  {msg}"

# ─────────────────────────────────────────────────────────────
# LOGGER CORE (SINGLETON)
# ─────────────────────────────────────────────────────────────
class Logger:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if self._initialized: return
        self._initialized = True

        self.logger = logging.getLogger("PMI")
        self.logger.setLevel(logging.DEBUG) # Базовый уровень ловим всё
        self.logger.propagate = False
        self.logger.handlers = []

        self.log_root = SharedConfig.get('paths.logs', '/opt/pmi/logs')
        os.makedirs(self.log_root, exist_ok=True)

        # ─── ОПРЕДЕЛЯЕМ РЕЖИМ ОТЛАДКИ ───
        # Приоритет: ENV > Config > False
        self.is_debug = (os.environ.get("PMI_DEBUG") == '1')
        if not self.is_debug:
            self.is_debug = SharedConfig.get('debug', False)

        # 1. CONSOLE HANDLER
        # Если DEBUG - льем всё. Если нет - только INFO.
        console_level = logging.DEBUG if self.is_debug else logging.INFO
       
        self.console_handler = logging.StreamHandler(sys.stdout)
        self.console_handler.setLevel(console_level)
        self.console_handler.setFormatter(ColorConsoleFormatter('%(message)s'))
        self.logger.addHandler(self.console_handler)

        # 2. SYSTEM LOG (pmi_system.log)
        # Если DEBUG - пишем INFO (но не дебаг, там будет ад). Иначе WARNING.
        sys_level = logging.INFO if self.is_debug else logging.WARNING
       
        sys_log_path = os.path.join(self.log_root, "pmi_system.log")
        self.sys_handler = RotatingFileHandler(sys_log_path, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8')
        self.sys_handler.setLevel(sys_level)
        self.sys_handler.setFormatter(PlainFileFormatter('%(asctime)s %(levelname)-5s %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
        self.logger.addHandler(self.sys_handler)

        self.session_handler = None
        self.current_session_id = None

        # Auto-attach
        env_run_id = os.environ.get("PMI_RUN_ID")
        if env_run_id:
            self.set_run_mode(env_run_id)

    def set_run_mode(self, session_id: str):
        if self.current_session_id == session_id:
            return

        if self.session_handler:
            self.logger.removeHandler(self.session_handler)
            self.session_handler.close()
            self.session_handler = None

        self.current_session_id = session_id
        session_dir = os.path.join(self.log_root, session_id)
        os.makedirs(session_dir, exist_ok=True)
       
        # Symlink update
        try:
            # 1. Линк на папку (уже было)
            link_dir = os.path.join(self.log_root, "latest")
            if os.path.islink(link_dir) or os.path.exists(link_dir):
                os.remove(link_dir)
            os.symlink(session_dir, link_dir)
            
            # 2. Линк на конкретный файл лога (ДОБАВЛЯЕМ!)
            link_file = os.path.join(self.log_root, "latest.log")
            target_log = os.path.join(session_dir, "pmi_session.log")
            
            # Создаем пустой файл, чтобы симлинк не был "битым", 
            # если оркестратор еще не успел туда ничего написать
            if not os.path.exists(target_log):
                with open(target_log, 'a') as f: pass
                
            if os.path.islink(link_file) or os.path.exists(link_file):
                os.remove(link_file)
            os.symlink(target_log, link_file)
            
        except: pass

        # 3. SESSION LOG (ВСЕГДА DEBUG)
        # Этот файл нужен для генерации отчетов. Он должен быть полным.
        session_log_path = os.path.join(session_dir, "pmi_session.log")
       
        self.session_handler = logging.FileHandler(session_log_path, mode='a', encoding='utf-8')
        self.session_handler.setLevel(logging.DEBUG)
        self.session_handler.setFormatter(PlainFileFormatter('%(asctime)s %(levelname)-5s %(message)s', datefmt='%H:%M:%S'))
       
        self.logger.addHandler(self.session_handler)
        self.session_handler.flush()

    def get_log_dir(self):
        if self.current_session_id:
            return os.path.join(self.log_root, self.current_session_id)
        return self.log_root

    def info(self, msg): self.logger.info(msg)
    def warning(self, msg): self.logger.warning(msg)
    def error(self, msg): self.logger.error(msg)
    def debug(self, msg): self.logger.debug(msg)
    def success(self, msg): self.logger.info(f"[OK] {msg}")

Log = Logger()