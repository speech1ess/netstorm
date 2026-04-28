#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import random
import copy
import os
import glob
import json
import subprocess

try:
    from shared import SharedConfig
    from pmi_logger import Log
except ImportError:
    import sys
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared import SharedConfig
    from pmi_logger import Log

def flatten_scenarios(raw_scenarios):
    """Разворачивает группы сценариев в плоский словарь"""
    flat_scenarios = {}
    for key, data in raw_scenarios.items():
        if isinstance(data, dict) and any(k in data for k in ['actors', 'template', 'duration', 'type']):
            flat_scenarios[key] = data
        elif isinstance(data, dict):
            for sc_id, sc_conf in data.items():
                if sc_id == 'label': continue 
                if isinstance(sc_conf, dict):
                    sc_conf['group_label'] = data.get('label', key)
                    flat_scenarios[sc_id] = sc_conf
    return flat_scenarios

def apply_preset_overrides(base_conf, overrides, is_custom=False):
    """Умный мерж: ищет акторов в корне, в template или в step"""
    
    # 🟢 ЕСЛИ ЭТО КАСТОМ - ВРУБАЕМ ЛОГИРОВАНИЕ
    if is_custom:
        from pmi_logger import Log # Убеждаемся, что логгер тут работает
        Log.info("\n============== 🕵️‍♂️ DEBUG КАСТОМНОГО МЕРЖА ==============")
        Log.info(f"Входящий JSON от фронта: {overrides}")
        Log.info("========================================================\n")

    for key, val in overrides.items():
        if key != 'actors': base_conf[key] = val
        
    # Ищем, где реально лежит список акторов
    target_block = base_conf
    if 'template' in base_conf and 'actors' in base_conf['template']:
        target_block = base_conf['template']
    elif 'step' in base_conf and 'actors' in base_conf['step']:
        target_block = base_conf['step']

    if 'actors' in overrides and 'actors' in target_block:
        actor_overrides = overrides['actors']
        for actor in target_block['actors']:
            # Ищем совпадение по имени или по профилю
            prof = actor.get('profile')
            name = actor.get('name')
            
            target_data = actor_overrides.get(name) or actor_overrides.get(prof)
            if target_data:
                
                # 🟢 СМОТРИМ, КТО КОГО БУДЕТ ПЕРЕЗАПИСЫВАТЬ
                if is_custom:
                    Log.info(f"🛠 Пытаемся накатить кастом на актора [{name or prof}]:")
                    Log.info(f"   Было в YAML (база): {actor}")
                    Log.info(f"   Прилетело с фронта: {target_data}\n")

                for k, v in target_data.items(): 
                    actor[k] = v

def resolve_val(val):
    """Распаковывает списки в случайные числа"""
    if isinstance(val, list) and len(val) == 2:
        try: return random.randint(int(val[0]), int(val[1]))
        except ValueError: return val
    return val

def resolve_config_values(template_conf, run_index=1):
    """Вычисляет шаги для серий (лесенка нагрузки)"""
    new_conf = copy.deepcopy(template_conf)
    def calc_step(val_obj):
        if isinstance(val_obj, dict) and 'start' in val_obj and 'step' in val_obj:
            return val_obj['start'] + (run_index - 1) * val_obj['step']
        return resolve_val(val_obj)

    if 'duration' in new_conf: new_conf['duration'] = calc_step(new_conf['duration'])
    if 'actors' in new_conf:
        for actor in new_conf['actors']:
            for key in ['override_mult', 'overridemult', 'override_tput', 'threads', 'delay', 'duration']:
                if key in actor: actor[key] = calc_step(actor[key])
    return new_conf

def build_cmd(tool, profile, duration, mult, tput, threads, log_name_base, actor_conf, profiles_dict, base_dir, lib_dir, python_bin, nodes_config):
    """Фабрика: собирает строку команды для subprocess"""
    Log.info(f"🚨 DEBUG [sc_utils]: Вход в build_cmd (tool={tool}, profile={profile})")
    
    # 🔴 ФИКС: Если у актора прописан свой duration, перезаписываем глобальный!
    if actor_conf and 'duration' in actor_conf:
        duration = actor_conf['duration']

    try:
        if tool == 'trex':
            # 🟢 Читаем путь к врапперу прямо из global.yaml!
            trex_proc = nodes_config.get('trex_node', {}).get('proc', {}).get('trex', {})
            script = trex_proc.get('wrapper', os.path.join(lib_dir, 'runners', 'trex_driver.py'))
            
            prof_data = profiles_dict.get('trex', {}).get(profile, {})
            Log.info(f"🚨 DEBUG [sc_utils]: Данные профиля из YAML: {prof_data}")
            if not prof_data:
                Log.error(f"🚨 DEBUG [sc_utils]: ВНИМАНИЕ! Профиль '{profile}' не найден в блоке profiles: trex!")
            script_name = prof_data.get('script', f"{profile}.py")
            full_profile_path = os.path.join(SharedConfig.get('paths.profiles', tool), f"{base_dir}/profiles/trex", script_name)
            
            tool_params = copy.deepcopy(prof_data.get('tunables', {}))
            if actor_conf and 'tunables' in actor_conf: tool_params.update(actor_conf['tunables'])
            
            # 👇 Теперь сюда подставится правильный duration (например, 15)
            cmd = [python_bin, script, full_profile_path, str(mult), str(duration), log_name_base, json.dumps(tool_params)]
            Log.info(f"TRex command generated: {' '.join(cmd)}")
            return cmd

        elif tool == 'jmeter':
            # (JMeter пока не трогаем, просто добавим чтение из конфига по аналогии, если он там есть)
            jmeter_proc = nodes_config.get('jmeter_node', {}).get('proc', {}).get('bin', {}) # Зависит от того, где он у тебя в yaml
            script = jmeter_proc.get('wrapper', os.path.join(lib_dir, 'runners', 'jmeter_driver.py'))
            prof_data = profiles_dict.get('jmeter', {}).get(profile, {})
            if not prof_data:
                Log.error(f"JMeter profile '{profile}' not found")
                return None
            
            if not threads: threads = prof_data.get('threads', 1)
            if not tput: tput = prof_data.get('throughput', 100)
            
            cmd = [python_bin, script, profile, str(threads), str(tput), str(duration), log_name_base]
            extras = prof_data.get('extra_args', "")
            if actor_conf and 'jprops' in actor_conf:
                for k, v in actor_conf['jprops'].items(): extras += f" -J{k}={v}"
            if actor_conf and 'payload' in actor_conf: extras += f" -JUPLOAD_FILE={actor_conf['payload']}"
            if extras: cmd.extend(extras.split())
            return cmd
    except Exception as e:
        import traceback
        print(f"🚨 CRITICAL ERROR [sc_utils]: Функция build_cmd упала с ошибкой: {e}")
        print(traceback.format_exc())
        return None
    return None

def _evaluate_health(runner, step_conf, run_index):
    """
    Гибридный Health-Check (Ping + TRex Logs).
    Возвращает КОРТЕЖ: (status_string, drops_count, ping_ok_boolean)
    """
    Log.info("\n🏥 --- Running Hybrid Health Check ---")
    dut_conf = runner.conf.get('program', {}).get('dut', {})

    # --- 1. ПРОВЕРКА CONTROL PLANE (Ping) ---
    ping_ok = True
    mgmt_ip = dut_conf.get('mgmt_ip')
    
    if mgmt_ip:
        # Пингуем: 1 пакет, таймаут 1 секунда. Вывод прячем.
        resp = subprocess.run(['ping', '-c', '1', '-W', '1', mgmt_ip], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ping_ok = (resp.returncode == 0)
        
        if not ping_ok:
            Log.warning(f"⚠️ [Control Plane] Ping to DUT ({mgmt_ip}) FAILED! Mgmt interface is unresponsive.")
        else:
            Log.success(f"✅ [Control Plane] DUT ({mgmt_ip}) is ALIVE.")
    else:
        Log.warning("⚠️ [Control Plane] 'mgmt_ip' not found in YAML. Skipping Ping.")

    # --- 2. ПРОВЕРКА DATA PLANE (TRex Logs) ---
    drops = 0
    log_found = False
    
    for actor in step_conf.get('actors', []):
        if actor.get('tool', '').lower() == 'trex':
            log_dir = os.path.join(SharedConfig.get('paths.logs', '/opt/pmi/logs'), runner.session_id)
            search_pattern = os.path.join(log_dir, f"trex_{actor.get('profile')}_run{run_index}_*.log")
            found_logs = glob.glob(search_pattern)
            
            if found_logs:
                log_path = found_logs[0] 
                log_found = True
                try:
                    with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                        lines = f.readlines()
                        tail = lines[-30:] 
                        
                        for line in tail:
                            if "Error" in line or "Traceback" in line:
                                Log.error("❌ [Data Plane] TRex script crashed (Error found in log).")
                                # 🟢 ИЗМЕНЕНИЕ: Возвращаем статус FATAL
                                return "FATAL", drops, ping_ok
                                
                            if "Drops:" in line:
                                try:
                                    drops_str = line.split("Drops:")[-1].strip()
                                    drops = int(drops_str)
                                except ValueError:
                                    pass
                except Exception as e:
                    Log.error(f"Failed to read log {log_path}: {e}")

    if not log_found:
        Log.warning("⚠️ Could not find TRex log to evaluate Data Plane. Proceeding blind.")
        # 🟢 ИЗМЕНЕНИЕ: Возвращаем статус OK
        return "OK", drops, ping_ok 

    Log.info(f"📊 [Data Plane] Detected Drops: {drops}")

    # --- 3. ПРИНЯТИЕ РЕШЕНИЯ (С ЧТЕНИЕМ ИЗ YAML) ---
    thresholds = dut_conf.get('thresholds') or dut_conf.get('tresholds') or {}
    
    WARN_LIMIT = int(thresholds.get('warn', 1000))
    FATAL_LIMIT = int(thresholds.get('fatal', 5000))
    
    Log.info(f"⚙️ Limits applied -> WARN: {WARN_LIMIT}, FATAL: {FATAL_LIMIT}")

    if drops < WARN_LIMIT:
        if not ping_ok:
            Log.info("Data Plane is clean! Ignoring Control Plane failure.")
        Log.success("🏥 Health Check Passed. Ready for next step.")
        # 🟢 ИЗМЕНЕНИЕ: Возвращаем статус OK
        return "OK", drops, ping_ok
        
    elif WARN_LIMIT <= drops < FATAL_LIMIT:
        # 🟡 ЖЕЛТАЯ ЗОНА: Возвращаем статус WARN, Оркестратор решит что делать
        Log.warning(f"🔥 ВНИМАНИЕ! Обнаружено {drops} потерь (Drops). Превышен WARN_LIMIT ({WARN_LIMIT}).")
        # 🟢 ИЗМЕНЕНИЕ: Убрали вызов UI из утилиты!
        return "WARN", drops, ping_ok
        
    else:
        # 🔴 КРАСНАЯ ЗОНА
        Log.error(f"💀 FATAL: {drops} drops exceed FATAL_LIMIT ({FATAL_LIMIT}). DUT is overwhelmed.")
        # 🟢 ИЗМЕНЕНИЕ: Возвращаем статус FATAL
        return "FATAL", drops, ping_ok