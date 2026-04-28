#!/usr/bin/env python3
import os
import sys
import subprocess
import shutil
import re

# Подключаем движок
try:
    from shared import Colors, SharedConfig
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import Colors, SharedConfig
    from pmi_logger import Log

base_dir = SharedConfig.get('paths.base', '/opt/pmi')
CONFIG_DIR = os.path.join(base_dir, 'config')
GLOBAL_YAML = os.path.join(CONFIG_DIR, 'global.yaml')
TEST_YAML = os.path.join(CONFIG_DIR, 'test_program.yaml')

# 🟢 УМНАЯ ПРОВЕРКА ОКРУЖЕНИЯ (Веб или Консоль)
IS_INTERACTIVE = sys.stdout.isatty() and not os.environ.get("PMI_WEB_MODE")

def _clear():
    """Очистка экрана с учетом режима отладки"""
    if not IS_INTERACTIVE: 
        return # 🟢 В вебе экран не чистим
        
    if SharedConfig.get('debug', False):
        print(f"\n{Colors.MAGENTA}--- DEBUG: Screen Clear skipped ---{Colors.ENDC}\n")
    else:
        os.system('clear')

def _print_header(title):
    print(f"\n{Colors.BOLD}{Colors.CYAN}=== {title} ==={Colors.ENDC}")

# ═══════════════════════════════════════════════════════════
# YAML PRETTY VIEWER
# ═══════════════════════════════════════════════════════════
class YamlViewer:
    @staticmethod
    def colorize(line):
        """Простая подсветка синтаксиса YAML"""
        # 1. Комментарии (Синим)
        if line.strip().startswith("#"):
            return f"{Colors.BLUE}{line}{Colors.ENDC}"
        
        # 2. Ключи (Cyan)
        line = re.sub(r'^(\s*)([\w\-\._]+):',
                      fr'\1{Colors.CYAN}\2{Colors.ENDC}:',
                      line)
        
        # 3. Значения (Green/Yellow)
        if ": " in line:
            parts = line.split(": ", 1)
            if not parts[1].strip().startswith("#"):
                if re.match(r'^[\d\.]+$|true|false|null', parts[1].strip(), re.I):
                    val_color = Colors.YELLOW
                else:
                    val_color = Colors.GREEN
                
                line = f"{parts[0]}: {val_color}{parts[1]}{Colors.ENDC}"
        
        # 4. Списки (Dashes)
        line = line.replace("- ", f"{Colors.MAGENTA}- {Colors.ENDC}")
        
        return line

    @staticmethod
    def show_file(filepath):
        if not os.path.exists(filepath):
            msg = f"File not found: {filepath}"
            Log.error(f"CONFIG: {msg}")
            print(f"{Colors.RED}{msg}{Colors.ENDC}")
            return

        with open(filepath, 'r') as f:
            lines = f.readlines()

        # 🟢 ЕСЛИ В ВЕБЕ: ПРОСТО ПЕЧАТАЕМ ВЕСЬ ФАЙЛ БЕЗ ПАУЗ И СТРАНИЦ
        if not IS_INTERACTIVE:
            print(f"{Colors.BOLD}{Colors.BLUE}=== VIEWER: {os.path.basename(filepath)} ==={Colors.ENDC}")
            for i, line in enumerate(lines):
                raw_line = line.rstrip()
                colored_line = YamlViewer.colorize(raw_line)
                line_num = f"{Colors.BOLD}{i + 1:4}{Colors.ENDC}"
                print(f"{line_num} | {colored_line}")
            print("\n")
            return

        try:
            term_size = shutil.get_terminal_size((80, 24))
            page_size = term_size.lines - 6
        except:
            page_size = 20

        total_lines = len(lines)
        current_line = 0
        
        while current_line < total_lines:
            _clear()
            print(f"{Colors.BOLD}{Colors.BLUE}=== VIEWER: {os.path.basename(filepath)} ==={Colors.ENDC}")
            print(f"{Colors.BLUE}Lines {current_line + 1}-{min(current_line + page_size, total_lines)} of {total_lines}{Colors.ENDC}")
            print(f"{Colors.BLUE}──────────────────────────────────────────────────────────────────{Colors.ENDC}")

            for i in range(page_size):
                if current_line + i >= total_lines: break
                raw_line = lines[current_line + i].rstrip()
                colored_line = YamlViewer.colorize(raw_line)
                line_num = f"{Colors.BOLD}{current_line + i + 1:4}{Colors.ENDC}"
                print(f"{line_num} | {colored_line}")

            print(f"{Colors.BLUE}──────────────────────────────────────────────────────────────────{Colors.ENDC}")
            
            if current_line + page_size >= total_lines:
                input(f"{Colors.GREEN}[END OF FILE] Press Enter to exit...{Colors.ENDC}")
                break
            
            nav = input(f"{Colors.YELLOW}[Enter]=Next Page, [q]=Quit > {Colors.ENDC}").lower()
            if nav == 'q': break
            current_line += page_size

# ═══════════════════════════════════════════════════════════
# MENU FUNCTIONS
# ═══════════════════════════════════════════════════════════

def view():
    Log.debug("CONFIG: entering VIEW mode")
    
    # 🟢 В вебе интерактивный выбор (input) не работает, выводим оба конфига
    if not IS_INTERACTIVE:
        YamlViewer.show_file(GLOBAL_YAML)
        YamlViewer.show_file(TEST_YAML)
        return

    while True:
        _clear()
        print(f"{Colors.BOLD}{Colors.CYAN}=== CONFIGURATION VIEWER ==={Colors.ENDC}")
        print("")
        print(f" {Colors.CYAN}1.{Colors.ENDC} Infrastructure ({Colors.BOLD}global.yaml{Colors.ENDC})")
        print(f" {Colors.CYAN}2.{Colors.ENDC} Test Logic ({Colors.BOLD}test_program.yaml{Colors.ENDC})")
        print(f" {Colors.CYAN}0.{Colors.ENDC} Back")
        
        choice = input(f"\n{Colors.BOLD}Select file > {Colors.ENDC}")

        if choice == '1': YamlViewer.show_file(GLOBAL_YAML)
        elif choice == '2': YamlViewer.show_file(TEST_YAML)
        elif choice == '0': return

def edit():
    # 🟢 Блокируем запуск Nano из веб-интерфейса
    if not IS_INTERACTIVE:
        print(f"{Colors.RED}❌ ERROR: Interactive Editor (Nano) cannot be launched from the Web Dashboard.{Colors.ENDC}")
        print("Please use SSH terminal to edit configuration files.")
        return

    Log.debug("CONFIG: entering EDIT mode")
    while True:
        _clear()
        print(f"{Colors.BOLD}{Colors.CYAN}=== CONFIGURATION EDITOR ==={Colors.ENDC}")
        print(f"{Colors.YELLOW}WARNING: Syntax errors may break the orchestrator!{Colors.ENDC}")
        print("")
        print(f" {Colors.CYAN}1.{Colors.ENDC} Edit Infrastructure (global.yaml)")
        print(f" {Colors.CYAN}2.{Colors.ENDC} Edit Test Logic (test_program.yaml)")
        print(f" {Colors.CYAN}0.{Colors.ENDC} Back")
        
        choice = input(f"\n{Colors.BOLD}Select file > {Colors.ENDC}")
        
        target_file = None
        if choice == '1': target_file = GLOBAL_YAML
        elif choice == '2': target_file = TEST_YAML
        elif choice == '0': return
        else: continue

        if target_file:
            Log.info(f"CONFIG: editing file {target_file}")
            subprocess.call(["nano", target_file])

def services():
    """
    Динамическая проверка сервисов из global.yaml
    """
    Log.debug("CONFIG: checking services status")
    _clear()
    print(f"{Colors.BOLD}{Colors.CYAN}=== SERVICES STATUS (From Config) ==={Colors.ENDC}\n")
    
    # Получаем словарь всех узлов
    nodes = SharedConfig.get('nodes', {})
    found_any = False

    for node_key, node_data in nodes.items():
        # Проверяем, есть ли у ноды секция proc
        procs = node_data.get('proc', {})
        if not procs:
            continue

        # Собираем список сервисов для этой ноды
        node_services = []
        for p_key, p_val in procs.items():
            # Это может быть просто путь к бинарнику, а может быть описание сервиса
            if isinstance(p_val, dict) and 'service_name' in p_val:
                node_services.append(p_val)
        
        if not node_services:
            continue

        found_any = True
        node_label = node_data.get('label', node_key)
        node_ip = node_data.get('net', {}).get('ip', 'local')

        # Заголовок группы (Ноды)
        print(f"{Colors.BOLD}{Colors.BLUE}Node: {node_label} ({node_ip}){Colors.ENDC}")

        for svc in node_services:
            name = svc['service_name']
            label = svc.get('label', name)
            
            # ВАЖНО: systemctl проверяет только ЛОКАЛЬНЫЕ сервисы.
            try:
                subprocess.check_call(["systemctl", "is-active", "--quiet", name])
                status = f"{Colors.GREEN}[ACTIVE]{Colors.ENDC}"
            except:
                Log.warning(f"SERVICE DOWN: {label} ({name}) on node {node_label} ({node_ip})")
                status = f"{Colors.RED}[DOWN]  {Colors.ENDC}"
                
            print(f"  {status} {label:<35} ({name})")
        
        print("") # Пустая строка между нодами

    if not found_any:
        msg = "No services defined in global.yaml (nodes -> proc -> service_name)"
        Log.warning(f"CONFIG: {msg}")
        print(f"{Colors.YELLOW}{msg}{Colors.ENDC}")

    # 🟢 В ВЕБЕ НЕ ЖДЕМ НАЖАТИЯ ENTER, ИНАЧЕ ПРОЦЕСС ЗАВИСНЕТ
    if IS_INTERACTIVE:
        input(f"\n{Colors.GREEN}Press Enter to return...{Colors.ENDC}")

# ═══════════════════════════════════════════════════════════
# УПРАВЛЕНИЕ СЕРВИСАМИ И СЕТЬЮ (НОВЫЙ БЛОК)
# ═══════════════════════════════════════════════════════════

def get_restart_menu():
    """Генератор меню для перезапуска сервисов (безопасно для Веба и TUI)"""
    nodes = SharedConfig.get('nodes', {})
    services_list = []

    for node_key, node_data in nodes.items():
        procs = node_data.get('proc', {})
        for p_key, p_val in procs.items():
            if isinstance(p_val, dict) and 'service_name' in p_val:
                services_list.append(p_val)

    items = []
    if not services_list:
        return [{"type": "command", "label": "No services found in config", "cmd": "echo 'Nothing to restart'"}]

    # Кнопка рестарта ВСЕХ сервисов
    items.append({
        "type": "python",
        "label": "🔄 Restart ALL Services",
        "module": "tools.config_manager",
        "function": "restart_service",
        "args": ["ALL"]
    })

    # Кнопки для каждого отдельного сервиса
    for svc in services_list:
        name = svc['service_name']
        label = svc.get('label', name)
        items.append({
            "type": "python",
            "label": f"► Restart: {label} ({name})",
            "module": "tools.config_manager",
            "function": "restart_service",
            "args": [name]
        })

    return items

def restart_service(service_name="ALL"):
    """Выполняет реальный рестарт одного или всех сервисов"""
    _clear()
    nodes = SharedConfig.get('nodes', {})
    services_to_restart = []

    for node_key, node_data in nodes.items():
        procs = node_data.get('proc', {})
        for p_key, p_val in procs.items():
            if isinstance(p_val, dict) and 'service_name' in p_val:
                if service_name == "ALL" or p_val['service_name'] == service_name:
                    services_to_restart.append(p_val)

    if not services_to_restart:
        print(f"{Colors.YELLOW}Service '{service_name}' not found in config.{Colors.ENDC}")
        if IS_INTERACTIVE: input(f"\n{Colors.GREEN}Press Enter...{Colors.ENDC}")
        return

    print(f"{Colors.BOLD}{Colors.YELLOW}=== RESTARTING PMI SERVICES ==={Colors.ENDC}\n")
    for svc in services_to_restart:
        name = svc['service_name']
        label = svc.get('label', name)
        print(f"🔄 Restarting {label} ({name})...", end=" ")
        sys.stdout.flush()
        try:
            subprocess.check_call(["sudo", "systemctl", "restart", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print(f"{Colors.GREEN}[OK]{Colors.ENDC}")
        except Exception as e:
            Log.error(f"Failed to restart {name}: {e}")
            print(f"{Colors.RED}[FAILED]{Colors.ENDC}")

    if IS_INTERACTIVE:
        input(f"\n{Colors.GREEN}Press Enter to return...{Colors.ENDC}")

def check_netns():
    """Динамическая проверка неймспейсов (netns), указанных в конфигурации"""
    Log.debug("CONFIG: checking network namespaces")
    _clear()
    print(f"{Colors.BOLD}{Colors.CYAN}=== NETWORK NAMESPACES (From Config) ==={Colors.ENDC}\n")

    nodes = SharedConfig.get('nodes', {})
    configured_netns = set()

    for node_key, node_data in nodes.items():
        netns = node_data.get('net', {}).get('netns')
        if netns:
            configured_netns.add(netns)

    if not configured_netns:
        print(f"{Colors.YELLOW}No custom namespaces (netns) defined in global.yaml.{Colors.ENDC}")
        if IS_INTERACTIVE: input(f"\n{Colors.GREEN}Press Enter...{Colors.ENDC}")
        return

    for ns in configured_netns:
        print(f"{Colors.BOLD}{Colors.BLUE}Namespace: {ns}{Colors.ENDC}")
        try:
            ns_list = subprocess.check_output(["ip", "netns", "list"]).decode('utf-8')
            if ns not in ns_list:
                print(f"  {Colors.RED}❌ Namespace '{ns}' is defined in config but NOT found in OS.{Colors.ENDC}")
                print("-" * 60)
                continue

            ip_a = subprocess.check_output(["ip", "netns", "exec", ns, "ip", "-br", "a"], stderr=subprocess.STDOUT).decode('utf-8').strip()
            routes = subprocess.check_output(["ip", "netns", "exec", ns, "ip", "route"], stderr=subprocess.STDOUT).decode('utf-8').strip()

            print(f"{Colors.CYAN}Interfaces & Addresses:{Colors.ENDC}")
            for line in ip_a.split('\n'):
                parts = line.split()
                # 🟢 Умное форматирование: парсим строку и считаем IP
                if len(parts) > 5: # Интерфейс + Статус + 3 адреса = 5. Если больше - режем.
                    intf = parts[0]
                    state = parts[1]
                    ips = parts[2:]
                    shown_ips = " ".join(ips[:3]) # Показываем только первые 3 адреса
                    hidden_count = len(ips) - 3
                    print(f"  {intf:<15} {state:<10} {shown_ips} ... {Colors.YELLOW}(+ {hidden_count} more aliases){Colors.ENDC}")
                else:
                    # Выравниваем стандартный вывод для красоты
                    if len(parts) >= 2:
                        intf = parts[0]
                        state = parts[1]
                        ips = " ".join(parts[2:])
                        print(f"  {intf:<15} {state:<10} {ips}")
                    else:
                        print(f"  {line}")

            print(f"{Colors.CYAN}Routes:{Colors.ENDC}")
            if routes:
                for line in routes.split('\n'):
                    print(f"  {line}")
            else:
                print("  (No routes configured)")

        except subprocess.CalledProcessError as e:
            print(f"  {Colors.RED}❌ Error querying namespace '{ns}': {e}{Colors.ENDC}")
            
        print("-" * 60)

    if IS_INTERACTIVE:
        input(f"\n{Colors.GREEN}Press Enter to return...{Colors.ENDC}")

if __name__ == "__main__":
    view()