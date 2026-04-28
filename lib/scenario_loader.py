#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import glob
try:
    from shared import SharedConfig
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import SharedConfig
    from pmi_logger import Log

DEFAULT_CONFIG = "test_program.yaml"
# Файл, в котором мы будем хранить имя активной программы (Share State)
STATE_FILE = os.path.join(SharedConfig.get('paths.config', '/opt/pmi/config'), ".active_pmi")

def _get_active_config():
    """Безопасное получение текущего конфига из общего файла состояния"""
    config_name = DEFAULT_CONFIG
    
    # Пытаемся прочитать имя файла из стейт-файла
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                saved_name = f.read().strip()
                if saved_name:
                    config_name = saved_name
        except Exception as e:
            Log.error(f"Failed to read state file: {e}")

    # Проверяем, существует ли сам конфиг реально. Если нет - откат на дефолт.
    config_path = os.path.join(SharedConfig.get('paths.config', '/opt/pmi/config'), config_name)
    if not os.path.exists(config_path):
        if config_name != DEFAULT_CONFIG:
            Log.warning(f"Config '{config_name}' not found, falling back to default.")
            # Если потеряли файл, перезаписываем стейт дефолтным
            set_active_program(DEFAULT_CONFIG)
            return DEFAULT_CONFIG
            
    return config_name

def set_active_program(filename):
    """
    Переключает активный файл программы.
    Сохраняет выбор в файл на диске, чтобы TUI и WEB воркеры были синхронизированы.
    """
    # 1. Сохраняем стейт на диск
    try:
        with open(STATE_FILE, 'w') as f:
            f.write(filename)
        Log.info(f"CONFIG: Active program saved to state file '{filename}'")
    except Exception as e:
        Log.error(f"Failed to write state file: {e}")
    
    # 2. Оставляем переменную окружения для обратной совместимости старого кода в текущем процессе
    os.environ["PMI_CURRENT_CONFIG"] = filename
    
    # 3. Пытаемся перезагрузить Runner
    try:
        from runners import scenario_runner
        if hasattr(scenario_runner, '_runner') and scenario_runner._runner:
            scenario_runner._runner.reload_config(filename)
        print(f"\nSUCCESS: Switched to {filename}")
    except Exception as e:
        Log.error(f"Failed to reload runner: {e}")
        print(f"\nERROR: Could not reload runner: {e}")

def get_program_selector_menu():
    """
    Генератор меню для выбора файла.
    """
    config_dir = SharedConfig.get('paths.config', '/opt/pmi/config')
    current_active = _get_active_config()
    
    # Ищем файлы конфигов
    patterns = [
        os.path.join(config_dir, "test_program*.yaml"),
        os.path.join(config_dir, "pmi_*.yaml")
    ]
    
    files = []
    for p in patterns:
        files.extend(glob.glob(p))
    
    # Убираем дубли и сортируем
    files = sorted(list(set(files)))
    
    menu_items = []
    
    for fpath in files:
        fname = os.path.basename(fpath)
        
        # Сравниваем с текущим активным из ENV
        is_active = (fname == current_active)
        prefix = "[*] " if is_active else "[ ] "
        
        # Добавляем визуальный стиль для активного элемента
        label_str = f"{prefix}{fname}"
        if is_active:
            label_str = f"{prefix}{fname} (Active)"

        menu_items.append({
            'label': label_str,
            'type': 'python',
            'module': 'scenario_loader',
            'function': 'set_active_program',
            'args': [fname]
        })
        
    if not menu_items:
        menu_items.append({'label': "No config files found!", 'type': 'exit'})
    
    # Кнопка возврата добавляется автоматически меню-билдером, но можно добавить Refresh
    menu_items.append({'label': "↻ Refresh List", 'type': 'generator', 'module': 'scenario_loader', 'function': 'get_program_selector_menu'})
        
    return menu_items

def get_dynamic_menu():
    """
    Строит ИДЕАЛЬНО ЧИСТОЕ меню на основе YAML (поддерживает и группы, и старый плоский формат).
    Прокидывает prompts из YAML в Web UI.
    """
    active_file = _get_active_config()
    Log.debug(f"LOADER: Building menu from {active_file}")
    
    conf = SharedConfig.load_yaml(active_file)
    if not conf:
        return [{'label': f"Error loading {active_file}", 'type': 'exit'}]

    raw_scenarios = conf.get('scenarios', {})
    dynamic_items = []
    
    # --- Вспомогательная функция для генерации кнопок одного сценария ---
    def build_scenario_item(sc_id, data):
        label = data.get('label', sc_id)
        presets = data.get('presets', {})
        
        # 🟢 1. ДОСТАЕМ PROMPT ИЗ YAML
        prompt_msg = data.get('prompt') 

        try:
            is_series_flag = bool(
                data.get('type') == 'series' or 
                'series' in data or 
                'repeats' in data or 
                int(data.get('iterations', 1) or 1) > 1
            )
        except Exception:
            is_series_flag = False

        # 🟢 2. ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ДЛЯ СОЗДАНИЯ КНОПКИ
        def create_btn(btn_label, btn_args):
            btn = {
                'label': btn_label,
                'type': 'python',
                'module': 'runners.scenario_runner',
                'function': 'run_scenario_by_id',
                'args': btn_args,
                'scen_id': sc_id
            }
            if prompt_msg:
                btn['confirm_msg'] = prompt_msg
                
            btn['is_series'] = is_series_flag

            # 🚀 СБОРКА ИДЕАЛЬНОЙ СХЕМЫ ДЛЯ ФРОНТЕНДА 🚀
            if len(btn_args) > 1 and btn_args[1] == 'custom':
                import copy
                
                # Берем базу (учитывая смарт-степперы)
                source = data.get('template', data) if is_series_flag else data
                schema_actors = copy.deepcopy(source.get('actors', []))
                
                # Подмешиваем поля из первого пресета (чтобы фронт узнал про tunables и mult)
                presets = data.get('presets', {})
                if presets and schema_actors:
                    first_p_overrides = presets[list(presets.keys())[0]].get('overrides', {}).get('actors', {})
                    
                    for act in schema_actors:
                        a_key = act.get('profile') or act.get('tool') or 'baseline'
                        ovr = first_p_overrides.get(a_key, {})
                        
                        # Вытягиваем нужные поля
                        for f in ['overridemult', 'threads', 'override_tput']:
                            if f in ovr and f not in act: 
                                act[f] = ovr[f]
                                
                        if 'tunables' in ovr:
                            if 'tunables' not in act: act['tunables'] = {}
                            for tk, tv in ovr['tunables'].items():
                                if tk not in act['tunables']: act['tunables'][tk] = tv
                
                # Упаковываем в чистейший чемоданчик без мусора и текстов
                btn['custom_schema'] = {
                    'duration': source.get('duration', ''),
                    'repeats': data.get('repeats', ''),
                    'interval': data.get('interval', ''),
                    'actors': schema_actors
                }
            
            return btn

        # 🟢 3. СОБИРАЕМ ПРЕСЕТЫ (ИЛИ ОДИНОЧНУЮ КНОПКУ)
        if presets:
            preset_items = []
            preset_items.append(create_btn("Default (As defined)", [sc_id, None]))
            preset_items.append(create_btn("⚙️ Custom Load (Interactive)", [sc_id, 'custom']))
            
            order = ['low', 'medium', 'high']
            sorted_presets = sorted(presets.keys(), key=lambda x: order.index(x) if x in order else 99)
            
            for p_key in sorted_presets:
                p_val = presets[p_key]
                p_label = p_val.get('label', p_key.capitalize())
                preset_items.append(create_btn(f"{p_label} Load", [sc_id, p_key]))
            
            return {'label': f"{sc_id}: {label}", 'type': 'submenu', 'items': preset_items}
        else:
            # Для одиночных сценариев возвращаем сразу кнопку
            return create_btn(f"{sc_id}: {label}", [sc_id, None])
    # ---------------------------------------------------------------------

    # СОБИРАЕМ ГРУППЫ И ОДИНОЧНЫЕ СЦЕНАРИИ (БЕЗ СОРТИРОВКИ, КАК В YAML)
    groups = {}
    ungrouped_items = []

    # raw_scenarios сохраняет порядок из YAML-файла
    for key, data in raw_scenarios.items():
        if not isinstance(data, dict): continue
        
        # Проверяем, сценарий это или группа
        if any(k in data for k in ['actors', 'template', 'duration', 'type']):
            ungrouped_items.append(build_scenario_item(key, data))
        else:
            # Это группа
            group_label = data.get('label', key)
            if group_label not in groups:
                groups[group_label] = []
            
            # Читаем сценарии внутри группы тоже по порядку YAML
            for sc_id, sc_conf in data.items():
                if sc_id == 'label' or not isinstance(sc_conf, dict): continue
                groups[group_label].append(build_scenario_item(sc_id, sc_conf))

    # ФОРМИРУЕМ ФИНАЛЬНОЕ МЕНЮ (СТРОГО В ПОРЯДКЕ ДОБАВЛЕНИЯ)
    if ungrouped_items:
        dynamic_items.append({
            'label': "Uncategorized Scenarios",
            'type': 'submenu',
            'items': ungrouped_items
        })
        
    for group_label, group_items in groups.items():
        if group_items: # Добавляем только если группа не пустая
            dynamic_items.append({
                'label': group_label,
                'type': 'submenu',
                'items': group_items
            })

    return dynamic_items