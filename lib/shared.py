#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import subprocess
import datetime
import shutil
import signal
import logging
import yaml
import builtins
from typing import Optional

try:
    from jinja2 import Template, Environment, FileSystemLoader, StrictUndefined
except ImportError:
    print("\033[91mCRITICAL ERROR: 'jinja2' library is missing.\033[0m")
    print("Please install it: pip3 install jinja2")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════
# 1. COLORS
# ═══════════════════════════════════════════════════════════
class Colors:
    RED     = '\033[0;31m'
    GREEN   = '\033[0;32m'
    YELLOW  = '\033[1;33m'
    BLUE    = '\033[0;34m'
    MAGENTA = '\033[0;35m'
    CYAN    = '\033[0;36m'
    BOLD    = '\033[1m'
    ENDC    = '\033[0m'
    DIM     = '\033[2m'

# ═══════════════════════════════════════════════════════════
# 2. CONFIG ENGINE (Jinja2 Version)
# ═══════════════════════════════════════════════════════════
class ConfigLoader:
    _instance = None
    _global_config = None
    _cache = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigLoader, cls).__new__(cls)
            cls._instance._load_global()
        return cls._instance

    def _load_global(self):
        """Loads global.yaml and renders it properly"""
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        config_path = os.path.join(base_dir, 'config', 'global.yaml')
        
        if not os.path.exists(config_path):
            # Fallback defaults if global.yaml missing
            self._global_config = {
                'paths': {'base': base_dir}
            }
            return

        # 1. Load Raw YAML to get context variables (like paths.base)
        with open(config_path, 'r') as f:
            raw_yaml = yaml.safe_load(f)
        
        # Ensure paths.base exists for template rendering
        if 'paths' not in raw_yaml: raw_yaml['paths'] = {}
        if 'base' not in raw_yaml['paths']: raw_yaml['paths']['base'] = base_dir

        # 2. Render YAML as Jinja2 Template using itself as context
        env = Environment(loader=FileSystemLoader(os.path.dirname(config_path)))
        template = env.from_string(open(config_path).read())
        rendered_yaml = template.render(raw_yaml)
        
        self._global_config = yaml.safe_load(rendered_yaml)

    def load_yaml(self, filename: str) -> dict:
        """Loads any YAML from config/ dir, rendering it with Global Context"""
        if filename in self._cache:
            return self._cache[filename]

        base_dir = self.get('paths.base')
        path = os.path.join(base_dir, 'config', filename)
        
        if not os.path.exists(path):
            return {}

        # Render using Global Config as Context
        with open(path, 'r') as f:
            template_str = f.read()
        
        t = Template(template_str)
        rendered = t.render(self._global_config)
        data = yaml.safe_load(rendered)
        
        self._cache[filename] = data
        return data

    def get(self, key_path: str, default=None):
        """Dot-notation access: get('paths.logs', '/tmp')"""
        keys = key_path.split('.')
        val = self._global_config
        try:
            for k in keys:
                val = val[k]
            return val
        except (KeyError, TypeError, AttributeError):
            return default

# ═══════════════════════════════════════════════════════════
# 3. TRAP MANAGER (Graceful Exit)
# ═══════════════════════════════════════════════════════════
class TrapManager:
    def __init__(self):
        self._callbacks = []
        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)

    def register(self, func):
        self._callbacks.append(func)

    def _handler(self, sig, frame):
        print(f"\n{Colors.YELLOW}Received Signal {sig}. Cleaning up...{Colors.ENDC}")
        for func in reversed(self._callbacks):
            try:
                func()
            except Exception as e:
                print(f"Cleanup error: {e}")
        
        # Trigger report generation on exit
        on_exit()
        sys.exit(0)

# ═══════════════════════════════════════════════════════════
# 4. GLOBAL EXIT HOOK (Reports Generation)
# ═══════════════════════════════════════════════════════════
def on_exit():
    """
    Global exit hook.
    Triggers background report generation using subprocess.Popen (Fire-and-forget).
    Requires PMI_RUN_ID env var.
    """
    try:
        # Only run if we are the Orchestrator (not a child process)
        if os.environ.get("PMI_IS_ORCH") != "1":
            return
            
        session_id = os.environ.get("PMI_RUN_ID")
        if not session_id:
            return

        base_dir = SharedConfig.get('paths.base', '/opt/pmi')
        lib_dir  = os.path.join(base_dir, 'lib')

        summary_script = os.path.join(lib_dir, 'reporting', 'generate_run_summary.py')
        index_script   = os.path.join(lib_dir, 'reporting', 'generate_index.py')

        # Chain commands: python summary.py ID && python index.py --generate
        cmds = []
        if os.path.exists(summary_script):
            cmds.append(f"{sys.executable} {summary_script} {session_id}")
        
        if os.path.exists(index_script):
            cmds.append(f"{sys.executable} {index_script} --generate")
            
        if cmds:
            full_cmd = " && ".join(cmds)
            # Запускаем в фоне (start_new_session=True отвязывает от текущего терминала)
            subprocess.Popen(
                full_cmd, 
                shell=True,
                stdout=subprocess.DEVNULL, 
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )

    except Exception:
        # Silently fail on exit to avoid spamming stderr
        pass

# 🟢 ГЛОБАЛЬНЫЙ ПРЕДОХРАНИТЕЛЬ ДЛЯ ВЕБ-РЕЖИМА
# Если загрузились под Веб-сервером, отключаем функцию input()
if os.environ.get("PMI_WEB_MODE") == "1":
    builtins.input = lambda prompt='': ''

# ═══════════════════════════════════════════════════════════
# INITIALIZATION (Order is Critical!)
# ═══════════════════════════════════════════════════════════

# 1. First: Config
SharedConfig = ConfigLoader()

# 2. Second: Logger
# IMPORTANT: We do NOT instantiate Logger here to avoid Circular Import.
# Modules must import 'Log' from 'pmi_logger.py' directly.
# Log = Logger()  <-- COMMENTED OUT INTENTIONALLY

# 3. Third: Trap Manager
SharedTrap = TrapManager()