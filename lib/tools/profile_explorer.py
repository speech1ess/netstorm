#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import datetime
import subprocess

try:
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log

# 🟢 УМНАЯ ПРОВЕРКА ОКРУЖЕНИЯ
IS_INTERACTIVE = sys.stdout.isatty() and not os.environ.get("PMI_WEB_MODE")

class TestExplorer:
    def __init__(self):
        self.base_dir = SharedConfig.get('paths.base', '/opt/pmi')
        self.profiles_dir = SharedConfig.get('paths.profiles', os.path.join(self.base_dir, 'profiles'))
        self.lib_dir = os.path.join(self.base_dir, 'lib')
        self.python_bin = sys.executable

    def _get_files(self, subdir, ext):
        target_dir = os.path.join(self.profiles_dir, subdir)
        if not os.path.exists(target_dir): return []
        return sorted([f for f in os.listdir(target_dir) if f.endswith(ext)])

    def _ask_param(self, prompt, default):
        val = input(f"{Colors.BOLD}{prompt} [{default}]: {Colors.ENDC}")
        return val.strip() if val.strip() else str(default)

    # 🟢 УМНЫЙ РЕВЕРС-ПОИСК: Ищем алиас профиля по имени файла
    def get_alias(self, tool, filepath):
        import os
        filename = os.path.basename(filepath)
        
        # Читаем актуальный конфиг
        active_config = os.environ.get("PMI_CURRENT_CONFIG", "test_program.yaml")
        conf = SharedConfig.load_yaml(active_config)
        profiles_dict = conf.get('profiles', {}).get(tool, {})
        
        for key, val in profiles_dict.items():
            # В новом формате профиль - это словарь
            if isinstance(val, dict):
                # Для jmeter ищем по ключу 'jmx', для trex по ключу 'script'
                target_file = val.get('jmx') if tool == 'jmeter' else val.get('script')
                if target_file and os.path.basename(str(target_file)) == filename:
                    return key
            # Поддержка старого формата (если где-то остался)
            elif isinstance(val, str) and os.path.basename(val) == filename:
                return key
                
        return None

    def _dry_run(self, tool, filepath, params):
        session_id = os.environ.get("PMI_RUN_ID", "20260406_ADHOC")
        test_ts = datetime.datetime.now().strftime("%H%M%S")
        profile_name = os.path.splitext(os.path.basename(filepath))[0]
        
        alias = self.get_alias(tool, filepath)
        display_name = alias if alias else profile_name
        
        log_name_base = f"{tool}_manual_{display_name}_{test_ts}"
        log_filename = f"{log_name_base}.log"
        
        cmd = []
        if tool == 'jmeter':
            runner = os.path.join(self.lib_dir, 'runners', 'jmeter_runner.py')
            # 🟢 Передаем filepath вместо алиаса
            cmd = [self.python_bin, runner, filepath, str(params.get('threads', 1)), str(params.get('tput', 100)), str(params.get('dur', 60)), log_name_base]
        elif tool == 'trex':
            runner = os.path.join(self.lib_dir, 'runners', 'trex_runner.py')
            # 🟢 Передаем filepath вместо алиаса
            cmd = [self.python_bin, runner, filepath, str(params.get('mult', 1)), str(params.get('dur', 60)), log_name_base]

        print(f"\n{Colors.YELLOW}════════ DRY RUN PREVIEW ════════{Colors.ENDC}")
        print(f" {Colors.BOLD}Tool:{Colors.ENDC}       {tool.upper()}")
        print(f" {Colors.BOLD}Profile:{Colors.ENDC}    {filepath}")
        if not alias:
            print(f" {Colors.YELLOW}Warning:{Colors.ENDC}    Profile not found in global.yaml! Using direct file path.")
        else:
            print(f" {Colors.BOLD}Alias:{Colors.ENDC}      {alias} (Found in yaml)")
            
        print(f" {Colors.BOLD}Session ID:{Colors.ENDC} {session_id}")
        print(f" {Colors.BOLD}Log Name:{Colors.ENDC}   {log_filename}")
        print(f" {Colors.BOLD}Params:{Colors.ENDC}     {params}")
        print(f"\n {Colors.BOLD}Full Command:{Colors.ENDC}")
        print(f" {Colors.CYAN}{' '.join(cmd)}{Colors.ENDC}")
        print(f"{Colors.YELLOW}═════════════════════════════════{Colors.ENDC}")
        
        if IS_INTERACTIVE:
            input(f"\n{Colors.GREEN}[DRY RUN] Press Enter to return to menu...{Colors.ENDC}")

    def _file_menu(self, tool, filepath):
        while True:
            fname = os.path.basename(filepath)
            print(f"\n{Colors.BLUE}──────────────────────────────────────{Colors.ENDC}")
            print(f"{Colors.BOLD} File: {fname}{Colors.ENDC}")
            print(f"{Colors.BLUE}──────────────────────────────────────{Colors.ENDC}")
            print(" 1. Run Test (Dry Run)")
            print(" 2. Edit (Nano)")
            print(" 0. Back")
            
            ans = input(f"\n{Colors.BOLD}Action > {Colors.ENDC}")
            if ans == '0': break
            
            if ans == '2': 
                os.system(f"nano {filepath}")
                continue
                
            if ans == '1':
                params = {}
                print(f"\n{Colors.CYAN}--- Configure Test Parameters ---{Colors.ENDC}")
                params['dur'] = self._ask_param("Duration (sec)", 60)
                if tool == 'jmeter':
                    params['threads'] = self._ask_param("Threads", 1)
                    params['tput'] = self._ask_param("Target RPS", 100)
                else:
                    params['mult'] = self._ask_param("Multiplier (e.g. 1, 0.5, 10pps)", 1)
                
                # 🟢 Если файла нет в активном конфиге, просто предупреждаем
                active_config = os.environ.get("PMI_CURRENT_CONFIG", "test_program.yaml")
                alias = self.get_alias(tool, filepath)
                
                if not alias:
                    print(f"\n{Colors.YELLOW}⚠️ Note: Profile '{os.path.basename(filepath)}' is not in {active_config}. Running as absolute path.{Colors.ENDC}")
                    
                self._dry_run(tool, filepath, params)

    def browse(self, tool):
        if not IS_INTERACTIVE:
            print(f"{Colors.RED}❌ ERROR: Interactive Explorer cannot be launched from Web. Use the Web Menu tree.{Colors.ENDC}")
            return

        ext = '.jmx' if tool == 'jmeter' else '.py'
        subdir = 'jmeter' if tool == 'jmeter' else 'trex'
        target_path = os.path.join(self.profiles_dir, subdir)
        
        if not os.path.exists(target_path):
            try: os.makedirs(target_path)
            except: pass
        
        while True:
            if not SharedConfig.get('debug', False): os.system('clear')
            print(f"{Colors.BOLD}=== {tool.upper()} EXPLORER ==={Colors.ENDC}")
            print(f"Folder: {Colors.BLUE}{target_path}{Colors.ENDC}\n")
            
            files = self._get_files(subdir, ext)
            if not files: print(f" {Colors.YELLOW}(No {ext} files found){Colors.ENDC}")
            for idx, f in enumerate(files): print(f" {Colors.CYAN}{idx + 1}.{Colors.ENDC} {f}")
            
            print(f"\n {Colors.GREEN}N. Create New Profile{Colors.ENDC}")
            print(f" {Colors.CYAN}0. Back to Main Menu{Colors.ENDC}")
            
            choice = input(f"\n{Colors.BOLD}Select > {Colors.ENDC}").strip()
            if choice == '0': break
            
            if choice.lower() == 'n':
                name = input("Enter filename (without extension): ").strip()
                if name:
                    path = os.path.join(target_path, f"{name}{ext}")
                    if not os.path.exists(path):
                        with open(path, 'w') as f:
                            if tool == 'trex': f.write("# TRex Stateless Profile\nfrom trex.stl.api import *\n\ndef register():\n    return STLS1()\n")
                            else: f.write("\n")
                    os.system(f"nano {path}")
                continue

            if choice.isdigit():
                idx = int(choice) - 1
                if 0 <= idx < len(files):
                    self._file_menu(tool, os.path.join(target_path, files[idx]))


# ═══════════════════════════════════════════════════════════
# WEB GENERATOR & EXECUTION HOOKS
# ═══════════════════════════════════════════════════════════
def get_explorer_menu():
    """Генерирует JSON-меню для навигации по профилям в Веб-интерфейсе"""
    base = SharedConfig.get('paths.base', '/opt/pmi')
    profiles_dir = SharedConfig.get('paths.profiles', os.path.join(base, 'profiles'))
    items = []
    
    if not os.path.exists(profiles_dir):
        return [{"type": "command", "label": "❌ Profiles directory not found", "cmd": "echo 'Dir missing'"}]

    for tool in sorted(os.listdir(profiles_dir)):
        tool_dir = os.path.join(profiles_dir, tool)
        if not os.path.isdir(tool_dir): continue

        tool_items = []
        for root, _, files in sorted(os.walk(tool_dir)):
            for file in sorted(files):
                if file.endswith('.py') or file.endswith('.jmx'):
                    filepath = os.path.join(root, file)
                    tool_items.append({
                        "type": "submenu",
                        "label": f"📄 {file}",
                        "items": [
                            {
                                "type": "command",
                                "label": "👁️ View Source (cat)",
                                "cmd": f"echo '--- FILE: {file} ---' && cat {filepath}"
                            },
                            {
                                "type": "python",
                                "label": "🚀 Run Ad-Hoc (Default Params)",
                                "module": "tools.profile_explorer",
                                "function": "web_run_profile",
                                "args": [tool, filepath]
                            },
                            # 🟢 NEW: Кнопка кастомного запуска
                            {
                                "type": "python",
                                "label": "⚙️ Run Ad-Hoc (Custom)",
                                "module": "tools.profile_explorer",
                                "function": "web_run_profile",
                                "args": [tool, filepath],
                                "tool": tool  # Передаем инструмент для умного модального окна
                            }
                        ]
                    })
        if tool_items:
            items.append({"type": "submenu", "label": f"📁 {tool.upper()} Profiles", "items": tool_items})
    return items

def web_run_profile(tool, filepath, custom_args_str=""):
    """Фоновый запуск профиля напрямую из Веб-интерфейса"""
    print(f"{Colors.BOLD}{Colors.CYAN}=== AD-HOC PROFILE EXECUTION ==={Colors.ENDC}")
    
    # 🟢 УБИРАЕМ ЖЕСТКУЮ ПРИВЯЗКУ К YAML
    alias = explorer.get_alias(tool, filepath)
    
    # Если есть алиас - отлично. Если нет - берем чистое имя файла.
    base_name = os.path.splitext(os.path.basename(filepath))[0]
    display_name = alias if alias else base_name
        
    print(f"Tool: {tool} | File: {os.path.basename(filepath)} | Display Name: {display_name}")
    
    # Дефолтные параметры
    params = {'dur': 60, 'tput': 100, 'threads': 1, 'mult': 1}
    generate_report = False  
    
    # ПАРСИМ КАСТОМНЫЕ ПАРАМЕТРЫ И ФЛАГ --report ИЗ ВЕБКИ
    if custom_args_str:
        import shlex
        print(f"Applying Custom Params: {custom_args_str}")
        c_args = shlex.split(custom_args_str)
        i = 0
        while i < len(c_args):
            if c_args[i] == '--duration' and i+1 < len(c_args):
                params['dur'] = c_args[i+1]
                i += 2
            elif c_args[i] == '--pps' and i+1 < len(c_args):
                params['tput'] = c_args[i+1]
                i += 2
            elif c_args[i] == '--mult' and i+1 < len(c_args):
                params['mult'] = c_args[i+1]
                i += 2
            elif c_args[i] == '--threads' and i+1 < len(c_args):
                params['threads'] = c_args[i+1]
                i += 2
            elif c_args[i] == '--report':
                generate_report = True
                i += 1
            else:
                i += 1

    print(f"\n{Colors.GREEN}🚀 Starting execution...{Colors.ENDC}")
    lib_dir = os.path.join(SharedConfig.get('paths.base', '/opt/pmi'), 'lib')
    runner = os.path.join(lib_dir, 'runners', f"{tool}_runner.py")
    
    test_ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    session_id = test_ts  
    os.environ["PMI_RUN_ID"] = session_id
    
    scen_id = "ADHOC"
    log_name = f"{tool}_{scen_id}_{display_name}_{test_ts}"
    
    # 🟢 ВАЖНО: ПЕРЕДАЕМ В РАННЕР filepath (АБСОЛЮТНЫЙ ПУТЬ), А НЕ АЛИАС!
    if tool == 'jmeter':
        cmd = [sys.executable, runner, filepath, str(params['threads']), str(params['tput']), str(params['dur']), log_name]
    else:
        cmd = [sys.executable, runner, filepath, str(params['mult']), str(params['dur']), log_name]
        
    try:
        subprocess.check_call(cmd)
        print(f"\n{Colors.GREEN}✅ Execution completed successfully!{Colors.ENDC}")
        
        if generate_report:
            print(f"\n{Colors.CYAN}📊 Triggering background report generation for session: {session_id}{Colors.ENDC}")
            summary_script = os.path.join(lib_dir, 'reporting', 'generate_run_summary.py')
            
            if os.path.exists(summary_script):
                subprocess.Popen(
                    [sys.executable, summary_script, session_id],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True
                )
                print(f"{Colors.GREEN}✅ Report task sent to background. Check 'ОТЧЕТЫ' tab soon!{Colors.ENDC}")
            else:
                print(f"{Colors.RED}❌ ERROR: Script generate_run_summary.py not found!{Colors.ENDC}")
                
    except subprocess.CalledProcessError as e:
        print(f"\n{Colors.RED}❌ Execution failed: {e}{Colors.ENDC}")

# Экземпляр и функции для TUI
explorer = TestExplorer()
def browse_jmeter(): explorer.browse('jmeter')
def browse_trex(): explorer.browse('trex')

if __name__ == "__main__":
    print("Select mode: 1. JMeter, 2. TRex")
    a = input("> ")
    if a == '1': browse_jmeter()
    elif a == '2': browse_trex()