#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import time
import os
import json
import copy
import sc_utils

try:
    from shared import Colors, SharedConfig
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared import Colors, SharedConfig
    from pmi_logger import Log
    from runners.sc_utils import _evaluate_health


BASE_DIR = SharedConfig.get('paths.base', '/opt/pmi')
SESSION_STATE_FILE = os.path.join(BASE_DIR, '.session_state.json')

def wait_for_user_signal(prompt_msg: str, allow_ips_toggle: bool = False) -> dict:
    """
    Приостанавливает выполнение сценария и ожидает подтверждения от пользователя.

    Поведение зависит от среды выполнения:
    - TUI (консоль): Ожидает нажатия клавиши Enter. Если allow_ips_toggle=True, 
      дополнительно запрашивает активацию инъекции малвари.
    - Web API (фоновый процесс): Создает файл состояния (.session_state.json) 
      со статусом 'pause', текстом промпта и флагом show_ips_toggle. Фронтенд 
      считывает этот файл, показывает диалоговое окно (при необходимости с чекбоксом) 
      и при нажатии 'OK' меняет статус на 'running', передавая выбранные опции 
      в объекте "options" (или удаляет файл). Функция непрерывно опрашивает файл 
      и при получении сигнала продолжает работу, очищая за собой стейт.
      
    Встроена защита от состояния гонки (race condition): старый файл стейта
    принудительно удаляется до инициализации новой паузы.
    
    Args:
        prompt_msg (str): Сообщение/инструкция, которая будет показана пользователю.
        allow_ips_toggle (bool): Флаг для отображения чекбокса активации IPS Overlay.

    Returns:
        dict: Словарь с опциями, выбранными пользователем (например, {'inject_ips': True}).
    """
    user_options = {}

    if sys.stdin.isatty():
        print(f"\n{Colors.YELLOW}>>> REQUIRED: {prompt_msg}{Colors.ENDC}")
        if allow_ips_toggle:
            ans = input(f"🔥 Включить инъекцию малвари (IPS Overlay) для этого шага? [y/N]: ").strip().lower()
            if ans == 'y':
                user_options['inject_ips'] = True
        input(f"{Colors.BOLD}>>> Press Enter to start this step...{Colors.ENDC}\n")
        return user_options
    else:
        Log.warning("Web Mode: Pausing session. Waiting for user action...")
        
        # 🟢 ЖЕЛЕЗОБЕТОННАЯ ОЧИСТКА ДО ПАУЗЫ (Защита от race conditions)
        if os.path.exists(SESSION_STATE_FILE):
            try: 
                os.remove(SESSION_STATE_FILE)
            except Exception: 
                pass
                
        # Небольшая пауза для гарантии синхронизации файловой системы (FS cache)
        time.sleep(0.5) 
        
        try:
            with open(SESSION_STATE_FILE, 'w') as f:
                json.dump({
                    "status": "pause", 
                    "prompt": prompt_msg,
                    "show_ips_toggle": allow_ips_toggle
                }, f)
        except Exception as e:
            Log.error(f"Failed to write state: {e}")
            return user_options
            
        while True:
            try:
                # Если файл исчез (удалил API), считаем это продолжением без доп. опций
                if not os.path.exists(SESSION_STATE_FILE):
                    break
                    
                with open(SESSION_STATE_FILE, 'r') as f:
                    state = json.load(f)
                    if state.get("status") == "running":
                        Log.success("Received resume signal from Web UI. Continuing...")
                        user_options = state.get("options", {})
                        break
            except Exception:
                pass # Игнорируем ошибки чтения (если файл в процессе перезаписи)
            
            time.sleep(1)
            
        # 🟢 ОЧИСТКА ПОСЛЕ ПАУЗЫ
        if os.path.exists(SESSION_STATE_FILE):
            try: 
                os.remove(SESSION_STATE_FILE)
            except Exception: 
                pass

        return user_options

def execute_scenario_logic(runner_instance, scenario_id: str, conf: dict, label: str, preset_name: str, is_batch: bool) -> bool:
    scen_type = conf.get('type', 'single')

    # =========================================================
    # 1. SMART LAUNCH SCREEN & INTERACTIVE TUNING
    # =========================================================
    if not is_batch:
        print(f"\n{Colors.CYAN}=========================================={Colors.ENDC}")
        print(f"{Colors.BOLD}🎯 TEST: {label}{Colors.ENDC}")
        print(f"{Colors.CYAN}=========================================={Colors.ENDC}")
        
        prompt_msg = conf.get('prompt')
        if prompt_msg:
            print(f"{Colors.YELLOW}>>> ACTION REQUIRED: {prompt_msg}{Colors.ENDC}\n")

        try:
            if not sys.stdin.isatty():
                Log.info("Web/API Mode detected. Skipping console prompt.")
            else:
                choice = 'i' if preset_name == 'custom' else input(f"Press {Colors.GREEN}[Enter]{Colors.ENDC} to start, {Colors.YELLOW}[I]{Colors.ENDC} to Interactive tune, or {Colors.RED}[Q]{Colors.ENDC} to Quit: ").strip().lower()
                
                if choice == 'q':
                    Log.warning("Aborted by user.")
                    return False
                    
                elif choice == 'i':
                    Log.info("Entering Interactive Custom Mode...")
                    print(f"\n{Colors.CYAN}=== CUSTOM TUNING ==={Colors.ENDC}")
                    print(f"{Colors.YELLOW}Press Enter to keep the current value in brackets [].{Colors.ENDC}")
                    
                    cur_dur = sc_utils.resolve_val(conf.get('duration', 60))
                    val = input(f"Global Duration (sec) [{cur_dur}]: ").strip()
                    if val: conf['duration'] = int(val)

                    for actor in conf.get('actors', []):
                        prof = actor.get('profile', 'unknown_profile')
                        tool = actor.get('tool', 'jmeter').lower()
                        
                        print(f"\n{Colors.BOLD}--- Target: {prof} ({tool.upper()}) ---{Colors.ENDC}")
                        
                        if tool == 'jmeter':
                            cur_tput = sc_utils.resolve_val(actor.get('override_tput', 100))
                            t_val = input(f"Target RPS [{cur_tput}]: ").strip()
                            if t_val: actor['override_tput'] = int(t_val)
                            
                            cur_th = sc_utils.resolve_val(actor.get('threads', 50))
                            th_val = input(f"Threads/Connections [{cur_th}]: ").strip()
                            if th_val: actor['threads'] = int(th_val)
                            
                        elif tool == 'trex':
                            cur_mult = sc_utils.resolve_val(actor.get('overridemult', 1))
                            m_val = input(f"TRex Multiplier (Mult) x 1000 Mp/s [{cur_mult}]: ").strip()
                            if m_val: actor['overridemult'] = int(m_val)
                            
        except ValueError:
            Log.error("Invalid number entered! Aborting run.")
            return False
        except KeyboardInterrupt:
            print(f"\n{Colors.RED}Run cancelled by user.{Colors.ENDC}")
            return False

    # =========================================================
    # 2. МАРШРУТИЗАЦИЯ ПО СТРАТЕГИЯМ
    # =========================================================
    if scen_type == 'series' or 'series' in conf:
        return _route_series(runner_instance, scenario_id, conf)
    else:
        # Простой single run
        if not is_batch and not conf.get('prompt'):
            if sys.stdin.isatty():
                input(f"{Colors.BOLD}>>> Press Enter to start single run...{Colors.ENDC}\n")
            
        runner_instance._execute_iteration(scenario_id, conf, run_index=None)
        return True

# ---------------------------------------------------------
# ПРИВАТНЫЕ СТРАТЕГИИ
# ---------------------------------------------------------

def _route_series(runner, scenario_id, conf):
    series_data = conf.get('series')
    
    if isinstance(series_data, list):
        Log.info(f"Starting List Series: {len(series_data)} steps")
        return _run_list_series(runner, scenario_id, conf, series_data)
        
    elif isinstance(series_data, int):
        Log.info(f"Starting Simple Repeat Series: {series_data} iterations")
        return _run_legacy_repeats(runner, scenario_id, conf, repeats=series_data)
        
    elif isinstance(series_data, dict):
        strategy = series_data.get('type', 'stepped')
        
        if strategy == 'stepped':
            Log.info("Starting Stepped Degradation Series")
            return _run_stepped_series(runner, scenario_id, conf, series_data)
            
        elif strategy == 'binary_search':
            Log.info("Starting Binary Search Series (RFC2544 style)")
            return _run_binary_search(runner, scenario_id, conf, series_data)
            
        else:
            Log.error(f"Unknown series strategy: {strategy}")
            return False
            
    else:
        repeats = conf.get('repeats', 1)
        Log.info(f"Starting Legacy Series: {repeats} iterations")
        return _run_legacy_repeats(runner, scenario_id, conf, repeats)

def _run_legacy_repeats(runner, scenario_id, conf, repeats):
    template = conf.get('step', conf.get('iteration', conf.get('template', {})))
    is_interactive = conf.get('interactive', False)
    
    raw_prompt = conf.get('prompt', '')
    prompt_lines = [line.strip() for line in raw_prompt.split('\n') if line.strip()]
    
    header_msg = prompt_lines[0] if prompt_lines else "Подготовьтесь к следующему шагу."
    steps_msgs = prompt_lines[1:] if len(prompt_lines) > 1 else []
    
    for i in range(1, repeats + 1):
        Log.info(f"--- Series Iteration {i}/{repeats} ---")
        
        user_opts = {}
        if is_interactive:
            step_msg = steps_msgs[i - 1] if i <= len(steps_msgs) else f"Шаг {i}/{repeats}. {header_msg}"
            display_msg = f"{header_msg}\n{step_msg}" if i == 1 else step_msg
            
            print(f"\n{Colors.YELLOW}>>> REQUIRED FOR STEP {i}: {display_msg}{Colors.ENDC}")
            # 🟢 Передаем allow_ips_toggle=True и ловим результат
            user_opts = wait_for_user_signal(display_msg, allow_ips_toggle=True)
            
        # 🟢 ПЕРЕДАЕМ TEMPLATE, КАК И ДОЛЖНО БЫТЬ!
        current_conf = sc_utils.resolve_config_values(template, run_index=i)
        
        # ==========================================
        # 🟢 МАГИЯ: ДИНАМИЧЕСКИЙ ИНЖЕКТ IPS OVERLAY
        # ==========================================
        if user_opts.get('inject_ips'):
            Log.info(f"💉 Внимание: Активирован Malware Overlay (IPS) для шага {i}!")
            for actor in current_conf.get('actors', []):
                # Ищем профили TRex, начинающиеся с astf_
                if actor.get('profile', '').startswith('astf_'):
                    if 'tunables' not in actor:
                        actor['tunables'] = {}
                    actor['tunables']['inject_malware'] = 1
        # ==========================================
        
        runner._execute_iteration(scenario_id, current_conf, run_index=i)
        
        # ==========================================
        # 🟢 3. HEALTH CHECK (УМНАЯ ОСТАНОВКА)
        # ==========================================
        should_check_health = conf.get('health_check', True)
        if should_check_health:
            try:
                # Читаем кортеж, который теперь возвращает утилита
                status, drops_count, is_ping_ok = sc_utils._evaluate_health(runner, current_conf, run_index=i)
                
                if status == "FATAL":
                    Log.error(f"\n🚨 DUT IS UNRESPONSIVE OR DROPPING TRAFFIC (Drops: {drops_count})! 🚨")
                    Log.warning(f"Stopping series early at iteration {i}. Maximum capacity reached.")
                    break # Прерываем цикл! Дальше не идем!
                    
                elif status == "WARN":
                    prompt_text = f"🔥 DUT теряет пакеты (Drops: {drops_count}). Control Plane: {'Мертв' if not is_ping_ok else 'Жив'}. Плавим железку дальше?"
                    # Здесь флаг allow_ips_toggle не передаем (по умолчанию False), чекбокс при варнинге не нужен
                    wait_for_user_signal(prompt_text)
                    Log.warning("User elected to continue. Initiating next wave...")
                    
            except Exception as e:
                import traceback
                Log.error(f"🚨 CRITICAL CRASH IN HEALTH CHECK: {e}")
                Log.error(traceback.format_exc())
                
        if i < repeats:
            _cooldown(runner, conf)
            
    return True

def _run_list_series(runner, scenario_id, conf, steps_list):
    # 🟢 Ищем 'step', если нет — 'iteration', если нет — старый 'template'
    template = conf.get('step', conf.get('iteration', conf.get('template', {})))
    for i, step_id in enumerate(steps_list, 1):
        Log.info(f"--- Series Step {i}/{len(steps_list)}: {step_id} ---")
        current_conf = sc_utils.resolve_config_values(template, run_index=i)
        runner._execute_iteration(scenario_id, current_conf, run_index=i)
        if i < len(steps_list):
            _cooldown(runner, conf)
    return True

def _run_stepped_series(runner, scenario_id, conf, data):
    steps = data.get('steps', [])
    require_confirm = data.get('require_confirm', True)
    
    if os.path.exists(SESSION_STATE_FILE):
        try: os.remove(SESSION_STATE_FILE)
        except: pass
    
    for i, step_def in enumerate(steps, 1):
        if isinstance(step_def, dict):
            step_id = step_def.get('id')
            custom_prompt = step_def.get('prompt')
        else:
            step_id = step_def
            custom_prompt = None

        Log.info(f"--- Degradation Step {i}/{len(steps)}: {step_id} ---")
        
        step_conf = runner.scenarios.get(step_id)
        if not step_conf:
            Log.error(f"Step config '{step_id}' not found! Skipping.")
            continue
            
        if require_confirm:
            prompt = custom_prompt or step_conf.get('prompt')
            if prompt:
                print(f"\n{Colors.YELLOW}>>> REQUIRED FOR STEP [{step_id}]: {prompt}{Colors.ENDC}")
                wait_for_user_signal(prompt)
                
        runner._execute_iteration(step_id, step_conf, run_index=i)
        
        if i < len(steps):
            _cooldown(runner, conf)
            
    return True

def _run_binary_search(runner, scenario_id, conf, data):
    min_val = data.get('min', 1)
    max_val = data.get('max', 100)
    precision = data.get('precision', 1)
    target_scenario = data.get('target', scenario_id)
    
    Log.info(f"\n{Colors.CYAN}=================================================={Colors.ENDC}")
    Log.info(f"🚀 СТАРТ: АВТОМАТИЧЕСКИЙ БИНАРНЫЙ ПОИСК (RFC 2544 style)")
    Log.info(f"Диапазон: [{min_val} - {max_val}], Точность: {precision}")
    Log.info(f"{Colors.CYAN}=================================================={Colors.ENDC}")
    
    best_pass = min_val
    iteration = 1
    max_iterations = 15 
    
    while (max_val - min_val) > precision and iteration <= max_iterations:
        mid_val = int((min_val + max_val) / 2) if precision >= 1 else (min_val + max_val) / 2.0
        Log.info(f"\n{Colors.CYAN}--- Binary Search Итерация {iteration} | Проверяем нагрузку: {mid_val} ---{Colors.ENDC}")
        
        try:
            # 🟢 Теперь copy импортирован, ошибка не вылетит!
            step_conf = copy.deepcopy(runner.scenarios.get(target_scenario, conf))
            
            for actor in step_conf.get('actors', []):
                if actor.get('tool') == 'trex': 
                    actor['overridemult'] = mid_val
                    actor['override_mult'] = mid_val
                elif actor.get('tool') == 'jmeter': 
                    actor['override_tput'] = mid_val
                    
            runner._execute_iteration(target_scenario, step_conf, run_index=mid_val)
            
            status, drops_count, is_ping_ok = sc_utils._evaluate_health(runner, step_conf, run_index=mid_val)
            
            if status == "OK":
                Log.success(f"✅ Нагрузка {mid_val} ВЫДЕРЖАНА (Drops: {drops_count}). Поднимаем нижнюю планку.")
                best_pass = mid_val
                min_val = mid_val 
            else:
                Log.warning(f"❌ Нагрузка {mid_val} ПРОВАЛЕНА (Drops: {drops_count}). Опускаем верхнюю планку.")
                max_val = mid_val 
                
        except Exception as e:
            import traceback
            Log.error(f"🚨 CRITICAL CRASH IN BINARY SEARCH: {e}")
            Log.error(traceback.format_exc())
            break # Если упали, прерываем поиск
            
        iteration += 1
        if (max_val - min_val) > precision:
            _cooldown(runner, conf)
        
    Log.success(f"\n{Colors.GREEN}=================================================={Colors.ENDC}")
    Log.success(f"🏁 БИНАРНЫЙ ПОИСК ЗАВЕРШЕН ЗА {iteration-1} ИТЕРАЦИЙ")
    Log.success(f"🏆 МАКСИМАЛЬНАЯ СТАБИЛЬНАЯ ПРОИЗВОДИТЕЛЬНОСТЬ: {best_pass}")
    Log.success(f"{Colors.GREEN}=================================================={Colors.ENDC}")
    
    return True

def _cooldown(runner, conf):
    interval_cfg = conf.get('interval', 0)
    sleep_time = sc_utils.resolve_val(interval_cfg)
    if sleep_time > 0:
        Log.info(f"Cooling down for {sleep_time}s...")
        time.sleep(sleep_time)