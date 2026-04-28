#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
import os
import time
import subprocess
import datetime
import copy
import json

try:
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log
    from target_monitor import TargetMonitor
    from tools.target_manager import CONF_BACKEND, NGINX_ENABLED
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log
    try:
        from monitoring.target_monitor import TargetMonitor
        from tools.target_manager import CONF_BACKEND, NGINX_ENABLED
    except ImportError:
        TargetMonitor = None
        CONF_BACKEND = "backend"
        NGINX_ENABLED = "/etc/nginx/sites-enabled/default"

# 🟢 ИМПОРТИРУЕМ НАШИ НОВЫЕ МОДУЛИ
import sc_logic
import sc_utils

class ScenarioRunner:
    def __init__(self):
        state_file = os.path.join(SharedConfig.get('paths.config', '/opt/pmi/config'), ".active_pmi")
        active_conf = "test_program.yaml"
        
        if "PMI_CURRENT_CONFIG" in os.environ:
            active_conf = os.environ["PMI_CURRENT_CONFIG"]
        elif os.path.exists(state_file):
            try:
                with open(state_file, 'r') as f:
                    saved = f.read().strip()
                    if saved: active_conf = saved
            except: pass

        Log.info(f"Runner initializing with config: {active_conf}")
        self.conf = SharedConfig.load_yaml(active_conf)
        self.profiles = self.conf.get('profiles', {})
        self.scenarios = sc_utils.flatten_scenarios(self.conf.get('scenarios', {})) # Утилита!
        
        self.base_dir = SharedConfig.get('paths.base', '/opt/pmi')
        self.lib_dir = os.path.join(self.base_dir, 'lib')
        self.python_bin = sys.executable
        self.processes = []
        SharedTrap.register(self.cleanup)
        
    def reload_config(self, config_filename):
        Log.info(f"Runner: Reloading configuration from {config_filename}...")
        try:
            new_conf = SharedConfig.load_yaml(config_filename)
            if not new_conf:
                Log.error("Runner: Config is empty or invalid!")
                return
            self.conf = new_conf
            self.profiles = self.conf.get('profiles', {})
            self.scenarios = sc_utils.flatten_scenarios(self.conf.get('scenarios', {})) # Утилита!
            Log.success(f"Runner: Configuration reloaded.")
        except Exception as e:
            Log.error(f"Runner: Failed to reload config: {e}")

    # ════════════════════════════════════════════════════════
    # SMART BATCH (API Adapter)
    # ════════════════════════════════════════════════════════
    def run_virtual_series(self, target_ids: list, preset_name: str = None, interval: int = 0):
        session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = session_id
        os.environ["PMI_RUN_ID"] = session_id
        Log.set_run_mode(session_id)

        Log.info(f"=== SMART BATCH START: {len(target_ids)} scenarios ===")
        virtual_conf = {"type": "series", "series": target_ids, "interval": interval}

        try:
            success = sc_logic.execute_scenario_logic(self, "BATCH_RUN", virtual_conf, "Batch Execution", preset_name, is_batch=True)
            if success:
                Log.success("Batch execution finished successfully.")
                self._trigger_report(session_id)
            return success
        except Exception as e:
            Log.error(f"Batch execution failed: {e}")
            self.cleanup()
            return False

    # ════════════════════════════════════════════════════════
    # SINGLE RUN (API Adapter)
    # ════════════════════════════════════════════════════════
    def run_scenario(self, scenario_id: str, preset_name: str = None, is_batch: bool = False, custom_overrides: dict = None) -> bool:
        if scenario_id not in self.scenarios:
            Log.error(f"Scenario '{scenario_id}' not found.")
            return False

        scenario_conf = copy.deepcopy(self.scenarios[scenario_id])
        scen_type = scenario_conf.get('type', 'single')
        label = scenario_conf.get('label', scenario_id)

        # --- БЛОК ОПРЕДЕЛЕНИЯ НАГРУЗКИ (КАСКАД) ---
        if preset_name and preset_name != 'custom':
            # 1. Запуск статического пресета (Low, Medium, High)
            presets = scenario_conf.get('presets', {})
            if preset_name in presets:
                preset_data = presets[preset_name]
                label = f"{label} ({preset_data.get('label', preset_name)})"
                sc_utils.apply_preset_overrides(scenario_conf, preset_data.get('overrides', {}))

        elif custom_overrides and 'custom-payload' in custom_overrides:
            # 2. Запуск из GUI Модалки (API Custom с JSON)
            raw_payload = custom_overrides.get('custom-payload')
            try:
                clean_json = raw_payload.strip("'\"")
                gui_data = json.loads(clean_json)
                
                sc_utils.apply_preset_overrides(scenario_conf, gui_data, is_custom=True)
                
                Log.info(f"✅ Настройки GUI применены: {gui_data}")
                label = f"{label} (API Custom Override)"
            except Exception as e:
                Log.error(f"❌ Ошибка парсинга --custom-payload от GUI: {e}")

        elif custom_overrides:
            # 3. Легаси запуск из консоли (CLI флагами типа --mult 70 --pps 5000)
            label = f"{label} (CLI Custom)"
            if 'duration' in custom_overrides: 
                scenario_conf['duration'] = custom_overrides['duration']
            
            for actor in scenario_conf.get('actors', []):
                tool = actor.get('tool', 'jmeter').lower()
                if tool == 'jmeter':
                    if 'pps' in custom_overrides: actor['override_tput'] = custom_overrides['pps']
                    if 'threads' in custom_overrides: actor['threads'] = custom_overrides['threads']
                elif tool == 'trex':
                    if 'mult' in custom_overrides:
                        actor['overridemult'] = custom_overrides['mult']
                        actor['override_mult'] = custom_overrides['mult']

        session_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_id = session_id
        os.environ["PMI_RUN_ID"] = session_id
        Log.set_run_mode(session_id)

        Log.info(f"=== ORCHESTRATOR START: {label} ===")
        time.sleep(0.5)

        try:
            success = sc_logic.execute_scenario_logic(self, scenario_id, scenario_conf, label, preset_name, is_batch)
            if success:
                Log.success(f"Orchestrator finished: {scenario_id}")
                self._trigger_report(session_id)
            return success
        except KeyboardInterrupt:
            self.cleanup()
            return False
        except Exception as e:
            self.cleanup()
            return False

    # ════════════════════════════════════════════════════════
    # CORE PROCESS EXECUTION
    # ════════════════════════════════════════════════════════
    def _execute_iteration(self, scenario_id, conf, run_index=None):
        duration = sc_utils.resolve_val(conf.get('duration', 60))
        actors = conf.get('actors', [])
        suffix = f"_run{run_index}" if run_index is not None else ""
        
        Log.info(f"Execution started. Duration: {duration}s")
        Log.info(f"🚨 DEBUG [Runner]: Входящий список actors: {actors}")

        monitor = None
        if TargetMonitor and os.path.exists(NGINX_ENABLED):
            if CONF_BACKEND in os.path.realpath(NGINX_ENABLED):
                monitor = TargetMonitor(Log.get_log_dir())
                monitor.start()

        current_processes = []
        open_files = []
        active_logs = []

        for actor in actors:
            Log.info(f"🚨 DEBUG [Runner]: Обрабатываем актора: {actor}")
            tool_ts = datetime.datetime.now().strftime("%H%M%S")
            tool = actor.get('tool')
            profile = actor.get('profile')
            delay = sc_utils.resolve_val(actor.get('delay', 0))
            
            mult = sc_utils.resolve_val(actor.get('override_mult', actor.get('overridemult', 1)))
            tput = sc_utils.resolve_val(actor.get('override_tput', 100))
            threads = sc_utils.resolve_val(actor.get('threads', 0))

            Log.info(f"🚨 DEBUG [Runner]: Извлекли: tool={tool}, profile={profile}, mult={mult}, tput={tput}")

            log_name_base = f"{tool}_{profile}{suffix}_{tool_ts}"
            log_path = os.path.join(Log.get_log_dir(), f"{log_name_base}.log")
            
            # Утилита фабрики команд!
            cmd = sc_utils.build_cmd(
                tool, profile, duration, mult, tput, threads, log_name_base, 
                actor, self.profiles, self.base_dir, self.lib_dir, self.python_bin,
                self.conf.get('nodes', {})
            )

            Log.info(f"🚨 DEBUG [Runner]: build_cmd вернул -> {cmd}")

            if not cmd:
                Log.error(f"🚨 DEBUG [Runner]: ПРОПУСК АКТОРА! Команда пустая.")
                continue

            if delay > 0:
                time.sleep(delay)

            try:
                f = open(log_path, 'w', encoding='utf-8')
                open_files.append(f)
                active_logs.append({'tool': tool, 'path': log_path})
                Log.info(f"Spawn: {tool.upper()} -> {log_name_base}.log")
                
                p = subprocess.Popen(cmd, stdout=f, stderr=subprocess.STDOUT, env=os.environ.copy())
                self.processes.append(p)
                current_processes.append(p)
                time.sleep(0.1)
            except Exception as e:
                Log.error(f"Failed to start {tool}: {e}")

        if current_processes: self._wait_and_tail_logs(current_processes, active_logs)
        for f in open_files: f.close()
        if monitor: monitor.stop()
        Log.info("Iteration execution finished.")
        return True

    def _wait_and_tail_logs(self, processes, log_files_info):
        print(f"\n{Colors.CYAN}--- Live Streaming Output ---{Colors.ENDC}")
        positions = {info['path']: 0 for info in log_files_info}
        
        while True:
            if all(p.poll() is not None for p in processes): break
            for info in log_files_info:
                path = info['path']
                tool = info['tool'].upper()
                try:
                    if os.path.exists(path):
                        with open(path, 'r', encoding='utf-8') as lf:
                            lf.seek(positions[path])
                            new_data = lf.read()
                            positions[path] = lf.tell()
                            if new_data:
                                for line in new_data.splitlines():
                                    if line.strip():
                                        color = Colors.GREEN if tool == 'JMETER' else Colors.MAGENTA
                                        print(f"{color}[{tool}]{Colors.ENDC} {line.strip()}")
                except Exception: pass
            time.sleep(1)

    def _trigger_report(self, session_id):
        script = os.path.join(self.lib_dir, 'reporting', 'generate_run_summary.py')
        Log.info(f"Looking for report script at: {script}") # 🟢 Посмотрим, куда он реально смотрит
        
        if os.path.exists(script):
            Log.info(f"Generating report in background for session {session_id}...")
            try:
                subprocess.Popen(
                    [self.python_bin, script, session_id],
                    stdout=subprocess.DEVNULL, 
                    start_new_session=True
                )
            except Exception as e:
                Log.error(f"Failed to spawn report process: {e}")
        else:
            Log.error(f"CRITICAL: Report script NOT FOUND at {script}!")

    def cleanup(self):
        if self.processes:
            for p in self.processes:
                if p.poll() is None:
                    try: p.terminate()
                    except: pass
            self.processes = []

_runner = ScenarioRunner()
def run_scenario_by_id(scen_id, preset=None): _runner.run_scenario(scen_id, preset)

if __name__ == "__main__":
    import builtins
    if len(sys.argv) > 1:
        args = sys.argv[1:]
        
        if "--api-batch" in args:
            args.remove("--api-batch")
            preset = None
            interval = 60
            if "--preset" in args:
                idx = args.index("--preset")
                preset = args.pop(idx+1)
                args.pop(idx)
            if "--interval" in args:
                idx = args.index("--interval")
                interval = int(args.pop(idx+1))
                args.pop(idx)
                
            if args: _runner.run_virtual_series(args, preset_name=preset, interval=interval)
            sys.exit(0)

        scen_id = args[0]
        preset = None
        is_batch = False
        custom_overrides = {}
        args = args[1:]
        
        if "--batch" in args:
            is_batch = True
            args.remove("--batch")
            builtins.input = lambda prompt='': ''
            
        if args and not args[0].startswith('--'):
            preset = args.pop(0)
            if preset.lower() in ['null', 'none', 'default']: preset = None

        i = 0
        while i < len(args):
            arg = args[i]
            if arg.startswith('--') and i + 1 < len(args):
                key = arg[2:]
                val = args[i+1]
                try: val = float(val) if '.' in val else int(val)
                except ValueError: pass
                custom_overrides[key] = val
                i += 2
            else: i += 1

        _runner.run_scenario(scen_id, preset_name=preset, is_batch=is_batch, custom_overrides=custom_overrides)