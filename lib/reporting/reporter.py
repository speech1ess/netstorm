#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import argparse
import traceback
from pathlib import Path

# Добавляем корень проекта в sys.path (Tech Debt: перевести на пакетную структуру)
pmi_lib = Path(__file__).resolve().parent.parent
if str(pmi_lib) not in sys.path:
    sys.path.insert(0, str(pmi_lib))

from shared import SharedConfig
from pmi_logger import Log
from reporting.strategies.ddos_strategy import DDoSReportStrategy
from reporting.strategies.ngfw_strategy import NGFWReportStrategy

# Реестр стратегий (Фабрика)
STRATEGIES = {
    'antiddos': DDoSReportStrategy,
    'ngfw': NGFWReportStrategy,
    'default': DDoSReportStrategy  # Фолбэк на старую логику
}

def load_session_config() -> str:
    """
    Безопасно определяет тип тестируемого устройства (DUT) из активного конфига.
    """
    config_dir = Path(SharedConfig.get('paths.config', '/opt/pmi/config'))
    state_file = config_dir / ".active_pmi"
    active_conf_name = "test_program.yaml"
    
    # Пытаемся прочитать стейт-файл без блокировки
    if state_file.exists():
        try:
            active_conf_name = state_file.read_text(encoding='utf-8').strip() or active_conf_name
        except IOError as e:
            Log.warning(f"[Reporter] Не удалось прочитать {state_file.name}: {e}")

    # Пытаемся загрузить YAML
    try:
        active_conf = SharedConfig.load_yaml(active_conf_name)
        if not active_conf:
            return 'default'
            
        # Спускаемся по словарю безопасно
        raw_test_type = active_conf.get('program', {}).get('dut', {}).get('type', 'default')
        return str(raw_test_type).lower()
        
    except Exception as e:
        # Здесь важно не проглотить синтаксическую ошибку YAML
        Log.error(f"[Reporter] Ошибка загрузки конфигурации {active_conf_name}: {e}")
        return 'default'


def main():
    parser = argparse.ArgumentParser(description="PMI Report Generator (Data-Driven Pipeline)")
    parser.add_argument("session_id", nargs="?", help="ID сессии (имя папки в logs/)")
    parser.add_argument("-t", "--type", type=str, help="Принудительно задать тип отчета (antiddos, ngfw)")
    args = parser.parse_args()

    # Определение Session ID
    import os # Оставляем os только для доступа к env
    session_id = args.session_id or os.environ.get("PMI_RUN_ID")
    if not session_id:
        parser.print_help()
        Log.error("Error: Обязательный параметр session_id не передан.")
        sys.exit(1)

    Log.info(f"[Reporter] Инициализация генератора отчетов для сессии: {session_id}")

    # Определение стратегии (Фабрика)
    if args.type:
        test_type = args.type.lower()
        Log.info(f"[Reporter] Тип отчета ПЕРЕОПРЕДЕЛЕН через CLI: '{test_type}'")
    else:
        test_type = load_session_config()

    StrategyClass = STRATEGIES.get(test_type)
    if not StrategyClass:
        Log.warning(f"[Reporter] Неизвестный тип DUT '{test_type}'. Используем fallback: default")
        StrategyClass = STRATEGIES['default']
    else:
        Log.info(f"[Reporter] Выбрана стратегия '{test_type}': {StrategyClass.__name__}")

    # Запуск конвейера генерации
    try:
        strategy = StrategyClass(session_id, config={})
        strategy.run_pipeline()
    except Exception as e:
        Log.error(f"[Reporter] Фатальный сбой пайплайна: {e}")
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()