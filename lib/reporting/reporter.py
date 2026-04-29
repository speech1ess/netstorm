#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import argparse
import traceback

pmi_lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if pmi_lib not in sys.path:
    sys.path.insert(0, pmi_lib)

from shared import SharedConfig
from pmi_logger import Log
from reporting.strategies.ddos_strategy import DDoSReportStrategy
from reporting.strategies.ngfw_strategy import NGFWReportStrategy

# Реестр стратегий (Фабрика)
STRATEGIES = {
    'antiddos': DDoSReportStrategy,
    'ngfw': NGFWReportStrategy,
    'default': DDoSReportStrategy # Фолбэк на старую логику
}

def main():
    # Настраиваем нормальный парсер аргументов командной строки
    parser = argparse.ArgumentParser(description="PMI Report Generator")
    parser.add_argument("session_id", nargs="?", help="ID сессии (папка в logs/)")
    parser.add_argument("-t", "--type", type=str, help="Принудительно задать тип отчета (antiddos, ngfw)")
    args = parser.parse_args()

    # Берем session_id из аргументов или из переменной окружения
    session_id = args.session_id or os.environ.get("PMI_RUN_ID")
    if not session_id:
        parser.print_help()
        Log.error("Error: session_id is required.")
        sys.exit(1)

    Log.info(f"[Reporter] Starting generation for session {session_id}...")

    # 1. Загружаем активный конфиг (по логике ScenarioRunner)
    try:
        # Узнаем, какой конфиг сейчас активен
        config_dir = SharedConfig.get('paths.config', '/opt/pmi/config')
        state_file = os.path.join(config_dir, ".active_pmi")
        active_conf_name = "test_program.yaml"
        
        if os.path.exists(state_file):
            with open(state_file, 'r') as f:
                saved = f.read().strip()
                if saved: active_conf_name = saved

        # Загружаем его
        active_conf = SharedConfig.load_yaml(active_conf_name)
        
        # Достаем тип DUT
        raw_test_type = active_conf.get('program', {}).get('dut', {}).get('type', 'default')
        config_test_type = str(raw_test_type).lower()
        
    except Exception as e:
        Log.warning(f"[Reporter] Could not load config meta. Error: {e}")
        config_test_type = 'default'
        active_conf = {}

    # 2. ОПРЕДЕЛЯЕМ ПРИОРИТЕТЫ (CLI флаг бьет YAML конфиг)
    if args.type:
        test_type = args.type.lower()
        Log.info(f"[Reporter] Strategy OVERRIDDEN by CLI flag: '{test_type}'")
    else:
        test_type = config_test_type

    # 3. Выбираем стратегию
    StrategyClass = STRATEGIES.get(test_type, STRATEGIES['default'])
    
    if test_type not in STRATEGIES:
        Log.warning(f"[Reporter] Unknown type '{test_type}'. Falling back to default: {StrategyClass.__name__}")
    else:
        Log.info(f"[Reporter] Selected strategy for type '{test_type}': {StrategyClass.__name__}")

    # 4. Запускаем конвейер
    try:
        strategy = StrategyClass(session_id, active_conf)
        strategy.run_pipeline()
    except Exception as e:
        Log.error(f"[Reporter] Pipeline crashed: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()