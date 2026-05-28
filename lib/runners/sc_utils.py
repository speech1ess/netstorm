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
    if not overrides:
        return

    # 🟢 ЕСЛИ ЭТО КАСТОМ - ВРУБАЕМ ЛОГИРОВАНИЕ
    if is_custom:
        from pmi_logger import Log # Убеждаемся, что логгер тут работает
        Log.info("\n============== 🕵️‍♂️ DEBUG КАСТОМНОГО МЕРЖА ==============")
        Log.info(f"Входящий JSON от фронта: {overrides}")
        Log.info("========================================================\n")

    # 1. Ищем, где реально лежат настройки теста (в корне, в template или в step)
    target_block = base_conf
    if 'template' in base_conf:
        target_block = base_conf['template']
    elif 'step' in base_conf:
        target_block = base_conf['step']

    # 2. Распределяем параметры верхнего уровня
    for key, val in overrides.items():
        if key == 'actors':
            continue # Акторы мы будем мержить отдельно ниже
            
        # Умный роутинг длительности (кладем туда же, где акторы)
        if key == 'duration':
            target_block['duration'] = val
        else:
            base_conf[key] = val

    # 3. Мержим акторов
    if 'actors' in overrides and 'actors' in target_block:
        actor_overrides = overrides['actors']
        for actor in target_block.get('actors', []):
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
    Гибридный Health-Check (Ping + TRex JSON Telemetry).
    Возвращает КОРТЕЖ: (status_string, deciding_drop_pct, ping_ok_boolean)
    """
    Log.info("\n🏥 --- Running Hybrid Health Check ---")
    dut_conf = runner.conf.get('program', {}).get('dut', {})

    # =========================================================================
    # 1. ПРОВЕРКА CONTROL PLANE (Ping & SSH via NetNS)
    # =========================================================================
    mgmt_ip = dut_conf.get('mgmt_ip')
    target_netns = SharedConfig.get('nodes.victim.net.netns', 'webserver')

    ping_ok, ssh_ok = False, False

    if not mgmt_ip:
        Log.warning("⚠️ [Control Plane] 'mgmt_ip' not found in YAML. Skipping Checks.")
    else:
        def check_port_in_netns(ns: str, ip: str, port: str = None) -> bool:
            if port:
                cmd = ['ip', 'netns', 'exec', ns, 'nc', '-z', '-w', '1', ip, str(port)]
            else:
                cmd = ['ip', 'netns', 'exec', ns, 'ping', '-c', '1', '-W', '1', ip]
            try:
                res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                return res.returncode == 0
            except subprocess.TimeoutExpired:
                Log.error(f"💀 [Control Plane] Timeout trying to reach {ip} from netns '{ns}'!")
                return False
            except Exception as e:
                Log.error(f"💀 [Control Plane] OS execution error in netns '{ns}': {e}")
                return False

        ping_ok = check_port_in_netns(target_netns, mgmt_ip)
        ssh_ok = check_port_in_netns(target_netns, mgmt_ip, port=22)

        if not ping_ok:
            Log.warning(f"⚠️ [Control Plane] Ping to DUT ({mgmt_ip}) from '{target_netns}' FAILED!")
        else:
            Log.success(f"✅ [Control Plane] Ping DUT ({mgmt_ip}) from '{target_netns}' is OK.")

        if not ssh_ok:
            Log.warning(f"⚠️ [Control Plane] SSH (Port 22) to DUT ({mgmt_ip}) from '{target_netns}' is CLOSED!")
        else:
            Log.success(f"✅ [Control Plane] SSH (Port 22) to DUT ({mgmt_ip}) from '{target_netns}' is OPEN.")

    # =========================================================================
    # 2. ПРОВЕРКА DATA PLANE (TRex JSON Stats via Unified Analyzer)
    # =========================================================================
    stats_found = False
    is_astf = False
    
    # Инициализируем переменные нулями для страховки
    l2_tx, l2_rx, l2_drops, l2_drop_pct = 0, 0, 0, 0.0
    l7_tx, l7_rx, l7_drops, l7_drop_pct = 0, 0, 0, 0.0
    ips_blocks, malware_sent = 0, 0  # 🟢 Добавили
    
    log_dir = os.path.join(SharedConfig.get('paths.logs', '/opt/pmi/logs'), runner.session_id)
    
    for actor in step_conf.get('actors', []):
        if actor.get('tool', '').lower() != 'trex':
            continue
            
        search_pattern = os.path.join(log_dir, f"trex_{actor.get('profile')}_run{run_index}_*.log")
        found_logs = glob.glob(search_pattern)
        
        if not found_logs:
            continue
            
        base_log_name = os.path.basename(found_logs[0]).replace('.log', '')
        stats_path = os.path.join(log_dir, f"stats_{base_log_name}.json")
        
        if os.path.exists(stats_path):
            stats_found = True
            try:
                # 🟢 ЕДИНЫЙ ИСТОЧНИК ПРАВДЫ (Обновленный Анализатор)
                from reporting.analyzers.trex_analyzer import TRexRunAnalyzer
                analyzer = TRexRunAnalyzer(stats_path)
                
                if analyzer.is_valid:
                    kpi = analyzer.get_kpi_summary()
                    is_astf = getattr(analyzer, 'is_astf', False)
                    
                    # Забираем физику
                    l2_tx = kpi.get('l2_tx_frames', 0)
                    l2_rx = kpi.get('l2_rx_frames', 0)
                    l2_drops = kpi.get('l2_drops', 0)
                    l2_drop_pct = kpi.get('l2_drop_pct', 0.0)
                    
                    # Забираем логику сессий
                    l7_tx = kpi.get('l7_tx_flows', 0)
                    l7_rx = kpi.get('l7_rx_flows', 0)
                    l7_drops = kpi.get('l7_drops', 0)
                    l7_drop_pct = kpi.get('l7_drop_pct', 0.0)
                    # 🟢 Забираем Security-метрики
                    ips_blocks = kpi.get('ips_blocks', 0)
                    malware_sent = kpi.get('malware_sent', 0)

            except Exception as e:
                Log.error(f"❌ [Data Plane] Failed to parse TRex telemetry via Analyzer: {e}")
                return "FATAL", 0.0, ping_ok

    # =========================================================================
    # 3. АНАЛИЗ ПОТЕРЬ И РЕШЕНИЕ
    # =========================================================================
    if not stats_found:
        Log.error("💀 FATAL: Could not find TRex JSON telemetry. Proceeding BLOCKED. Generator failed?")
        return "FATAL", 0.0, ping_ok 

    if l2_tx == 0 and l7_tx == 0:
        Log.error("💀 FATAL: TRex reported 0 TX packets/flows. No traffic was generated.")
        return "FATAL", 0.0, ping_ok

    thresholds = dut_conf.get('thresholds') or {}
    WARN_LIMIT = float(thresholds.get('warn', 0.05))
    FATAL_LIMIT = float(thresholds.get('fatal', 0.1))

    # Выводим кристально чистые логи
    Log.info(f"📊 [Data Plane - L2] Frames TX: {l2_tx} | RX: {l2_rx} | Drops: {l2_drops} ({l2_drop_pct:.4f}%)")
    
    if is_astf:
        Log.info(f"📊 [Data Plane - L7] Legit Sessions TX: {l7_tx} | RX: {l7_rx} | Drops: {l7_drops} ({l7_drop_pct:.4f}%)")
        
        # 🟢 Выводим статистику IPS, если малварь была в запуске
        if malware_sent > 0:
            block_pct = (ips_blocks / malware_sent) * 100.0
            SEC_BLOCK_MIN = float(thresholds.get('malware_block', 100.0)) # Исправлена опечатка
            
            Log.info(f"🛡️  [Security Plane] Malware Sent: {malware_sent} | IPS Blocks: {ips_blocks} | Block Rate: {block_pct:.2f}%")
            Log.info(f"⚙️ Limits applied -> WARN: {WARN_LIMIT}%, FATAL: {FATAL_LIMIT}%, SEC_MIN: {SEC_BLOCK_MIN}%")
        else:
            Log.info(f"⚙️ Limits applied -> WARN: {WARN_LIMIT}%, FATAL: {FATAL_LIMIT}%")

    # =========================================================================
    # 🟢 4. ЛОГИКА СУДЕЙСТВА: Умный алгоритм (NetSecOPEN / RFC 9411 Style)
    # =========================================================================
    PANIC_THRESHOLD = 98.0  # Черная дыра (инфраструктура лежит)
    L2_TOLERANCE = 1.0      # Допустимый фон микродропов (TCP Retransmits). Обычно 1%.

    if is_astf:
        if l2_drop_pct >= PANIC_THRESHOLD:
            Log.error(f"☢️ PANIC: Catastrophic L2 loss detected ({l2_drop_pct:.2f}%). Network is blackholing traffic!")
            return "CRITICAL", l2_drop_pct, ping_ok

        # Если L2 потери превышают допустимый фоновый шум — это железная деградация сети
        if l2_drop_pct >= L2_TOLERANCE:
            deciding_drop_pct = l2_drop_pct
            fail_reason = "L2 Frames (Exceeded L2 Tolerance)"
        else:
            # L2 потери в рамках нормы (справляется TCP). Судим СТРОГО по L7!
            deciding_drop_pct = l7_drop_pct
            fail_reason = "L7 Sessions"
            if l2_drop_pct > 0 and l7_drop_pct < FATAL_LIMIT:
                Log.info(f"💡 NetSecOPEN: TCP Stack handled {l2_drop_pct:.4f}% L2 background drops. Judging strictly by L7 Transaction Success Rate.")
    else:
        # Для Stateless (STL) тестов (чистый UDP флуд) судим только по физике
        if l2_drop_pct >= PANIC_THRESHOLD:
            Log.error(f"☢️ PANIC: Catastrophic L2 loss detected ({l2_drop_pct:.2f}%).")
            return "CRITICAL", l2_drop_pct, ping_ok
            
        deciding_drop_pct = l2_drop_pct
        fail_reason = "L2 Frames"

    # =========================================================================
    # 5. ПРИНЯТИЕ ФИНАЛЬНОГО РЕШЕНИЯ
    # =========================================================================
    if is_astf and malware_sent > 0:
        if block_pct < SEC_BLOCK_MIN:
            Log.error(f"💀 FATAL: Security Bypass! Blocked only {block_pct:.2f}% (Required: {SEC_BLOCK_MIN}%). DUT operates as a dumb router!")
            return "FATAL", deciding_drop_pct, ping_ok

    if deciding_drop_pct < WARN_LIMIT:
        if not ping_ok:
            Log.info("Data Plane is clean! Ignoring Control Plane failure.")
        Log.success(f"🏥 Health Check Passed (Max legit drops: {deciding_drop_pct:.4f}%). Ready for next step.")
        return "OK", deciding_drop_pct, ping_ok
        
    elif WARN_LIMIT <= deciding_drop_pct < FATAL_LIMIT:
        Log.warning(f"🔥 ВНИМАНИЕ! Обнаружено {deciding_drop_pct:.4f}% потерь ({fail_reason}). Превышен WARN_LIMIT ({WARN_LIMIT}%).")
        return "WARN", deciding_drop_pct, ping_ok
        
    else:
        Log.error(f"💀 FATAL: {deciding_drop_pct:.4f}% drops ({fail_reason}) exceed FATAL_LIMIT ({FATAL_LIMIT}%). DUT is overwhelmed.")
        return "FATAL", deciding_drop_pct, ping_ok

def has_malware_capability(conf: dict) -> bool:
    """Проверяет наличие L7 ASTF EMIX профилей в конфиге."""
    # 🟢 ФИКС: Используем list(), чтобы создать независимую копию и не мутировать исходный словарь!
    actors = list(conf.get('actors', []))
    
    for tpl_key in ['template', 'step', 'iteration']:
        if tpl_key in conf:
            actors.extend(conf[tpl_key].get('actors', []))

    for actor in actors:
        if actor.get('tool', '').lower() == 'trex' and actor.get('profile', '').lower().startswith('astf_emix_'):
            return True
    return False

def apply_malware_overlay(conf: dict):
    """Data-Driven мутатор: включает IPS для всех поддерживаемых акторов в конфиге."""
    def _inject(actors_list):
        for actor in actors_list:
            if actor.get('profile', '').startswith('astf_emix_'):
                if 'tunables' not in actor: 
                    actor['tunables'] = {}
                actor['tunables']['inject_malware'] = 1

    _inject(conf.get('actors', []))
    for tpl_key in ['template', 'step', 'iteration']:
        if tpl_key in conf:
            _inject(conf[tpl_key].get('actors', []))

def dump_session_meta(session_dir: str, active_config: dict) -> None:
    """
    Дамп иммутабельного контекста (Presentation Config) для репортера.
    Фиксируем только те данные, которые нужны для оценки и рендера.
    """
    try:
        program_conf = active_config.get('program', {})
        dut_conf = program_conf.get('dut', {})
        
        # Формируем строгий DTO
        meta_payload = {
            "description": program_conf.get('description', 'Auto-generated load test'),
            "dut_label": dut_conf.get('label', 'Unknown DUT'),
            "dut_type": dut_conf.get('type', 'Unknown'),
            # Берем лимиты или ставим безопасные дефолты
            "thresholds": dut_conf.get('thresholds') or dut_conf.get('tresholds') or {'warn': 0.05, 'fatal': 0.1}
        }
        
        meta_path = os.path.join(session_dir, 'session_meta.json')
        with open(meta_path, 'w', encoding='utf-8') as f:
            # ensure_ascii=False важен, т.к. у тебя русские символы в description
            json.dump(meta_payload, f, indent=2, ensure_ascii=False)
            
        Log.info(f"💾 [State] Session metadata frozen at {meta_path}")
        
    except Exception as e:
        Log.error(f"❌ [State] Failed to dump session meta: {e}")