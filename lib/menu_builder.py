###/opt/pmi/lib/menu_builder.py
#!/usr/bin/env python3
import os
import sys
import importlib
import time
import traceback

try:
    from shared import Colors, SharedConfig, SharedTrap, on_exit
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import Colors, SharedConfig, SharedTrap, on_exit
    from pmi_logger import Log


try:
    from monitoring.system_status import SystemStatus
except ImportError:
    # Не критично, если нет модуля статуса, но предупредим
    print(f"{Colors.RED}WARNING: system_status.py not found.{Colors.ENDC}")
    SystemStatus = None


class MenuBuilder:
    def __init__(self):
        self.menu_conf = SharedConfig.load_yaml('menu_structure.yaml')
        if not self.menu_conf:
            msg = "Error: menu_structure.yaml is empty or missing."
            Log.error(f"MENU: {msg}")
            print(f"{Colors.RED}{msg}{Colors.ENDC}")
            sys.exit(1)

        self.title = self.menu_conf.get('title', 'PMI ORCHESTRATOR')
        
        # Если статус есть - инициализируем
        self.status = SystemStatus() if SystemStatus else None

        # Читаем флаг дебага из глобального конфига
        self.is_debug = SharedConfig.get('debug', False)

    def _clear_screen(self):
        # В режиме дебага экран не чистим, чтобы видеть логи предыдущих команд
        if not self.is_debug:
            os.system('clear')
        else:
            print(f"\n{Colors.MAGENTA}--- DEBUG: Screen Clear skipped ---{Colors.ENDC}\n")

    def _print_dashboard(self):
        self._clear_screen()
        if self.status:
            try:
                print(self.status.get_dashboard())
            except Exception as e:
                Log.error(f"DASHBOARD ERROR: {e}")
                print(f"{Colors.RED}[DASHBOARD ERROR] {e}{Colors.ENDC}")
                if self.is_debug:
                    traceback.print_exc()

    def _execute_action(self, item):
        """Выполнение действия по клику"""
        action_type = item.get('type')
        label = item.get('label', 'Action')

        if self.is_debug:
            print(f"{Colors.MAGENTA}[DEBUG] Action: {action_type} | Label: {label}{Colors.ENDC}")
        Log.debug(f"MENU: action_type={action_type}, label={label}")

        try:
            if action_type == 'exit':
                Log.info("MENU: exit requested by user")
                print(f"\n{Colors.GREEN}Exiting. Good luck!{Colors.ENDC}")
                try:
                    on_exit()
                except Exception:
                    pass
                sys.exit(0)

            elif action_type == 'submenu':
                Log.debug(f"MENU: entering submenu '{label}'")
                self.run(item.get('items', []), parent_label=item.get('label'))

            # [FIX] ДОБАВЛЕН БЛОК GENERATOR (без него динамика не работает)
            elif action_type == 'generator':
                module_name = item.get('module')
                func_name = item.get('function')
                
                Log.info(f"MENU: generating dynamic submenu from {module_name}.{func_name}")
                try:
                    mod = importlib.import_module(module_name)
                    # Перезагружаем для подхвата изменений "на лету"
                    if self.is_debug: importlib.reload(mod)
                    
                    if hasattr(mod, func_name):
                        generator_func = getattr(mod, func_name)
                        # Функция должна вернуть список словарей (пунктов меню)
                        generated_items = generator_func()
                        
                        # Открываем полученный список как подменю
                        self.run(generated_items, parent_label=label)
                    else:
                        print(f"{Colors.RED}Generator function not found: {func_name}{Colors.ENDC}")
                        input("Press Enter...")
                except Exception as e:
                    Log.error(f"Generator failed: {e}")
                    print(f"{Colors.RED}Generator Error: {e}{Colors.ENDC}")
                    if self.is_debug: traceback.print_exc()
                    input("Press Enter...")

            elif action_type == 'python':
                module_name = item.get('module')
                func_name = item.get('function')
                # [FIX] Поддержка аргументов (Args)
                args = item.get('args', [])

                Log.info(f"MENU: python action {module_name}.{func_name}(args={args})")
                if self.is_debug:
                    print(f"{Colors.MAGENTA}[DEBUG] Import: {module_name} -> Call: {func_name}({args}){Colors.ENDC}")

                # Импорт на лету
                mod = importlib.import_module(module_name)
                # Перезагружаем модуль в debug-режиме, чтобы подхватывать правки кода без рестарта меню
                if self.is_debug:
                    importlib.reload(mod)

                if hasattr(mod, func_name):
                    func = getattr(mod, func_name)
                    print(f"\n{Colors.YELLOW}>>> Executing: {label}...{Colors.ENDC}")
                    
                    # === ЗАПУСК СЦЕНАРИЯ ===
                    # [FIX] Передаем аргументы, если они есть
                    if args:
                        func(*args)
                    else:
                        func() 
                    
                    input(f"\n{Colors.GREEN}[DONE] Press Enter to return...{Colors.ENDC}")
                else:
                    Log.error(f"Function {func_name} not found in {module_name}")
                    print(f"{Colors.RED}Error: Function {func_name} not found{Colors.ENDC}")
                    input("Press Enter...")

            elif action_type == 'command':
                cmd = item.get('cmd')
                Log.info(f"MENU: shell command '{cmd}'")
                if self.is_debug:
                    print(f"{Colors.MAGENTA}[DEBUG] Shell: {cmd}{Colors.ENDC}")

                print(f"\n{Colors.YELLOW}>>> Running: {cmd}{Colors.ENDC}")
                os.system(cmd)
                input(f"\n{Colors.GREEN}[DONE] Press Enter...{Colors.ENDC}")

        except KeyboardInterrupt:
            print(f"\n{Colors.YELLOW}Action cancelled by user.{Colors.ENDC}")

        except Exception as e:
            # ВОТ ОНО: Если дебаг включен, показываем всё мясо
            Log.error(f"MENU: execution failed: {e}")
            print(f"\n{Colors.RED}ERROR EXECUTION: {e}{Colors.ENDC}")
            if self.is_debug:
                print(f"\n{Colors.RED}=== TRACEBACK (DEBUG MODE) ==={Colors.ENDC}")
                traceback.print_exc()
                print(f"{Colors.RED}=============================={Colors.ENDC}")

            input("Press Enter to continue...")

    def run(self, menu_items=None, parent_label=None):
        if menu_items is None:
            menu_items = self.menu_conf.get('items', [])

        while True:
            # Если что-то упало при отрисовке меню - ловим тут
            try:
                self._print_dashboard()

                header = f"MENU: {parent_label}" if parent_label else self.title
                print(f"{Colors.BOLD}{header}{Colors.ENDC}")

                # [NEW LOGIC] Separate default item from the rest for preset menus
                default_item = None
                display_items = []
                for item in menu_items:
                    # The default action for presets is identified by this specific label
                    if "Default (As defined)" in item.get('label', ''):
                        default_item = item
                    else:
                        display_items.append(item)
                
                # If no default item was found, behave as before
                if not default_item:
                    display_items = menu_items

                for idx, item in enumerate(display_items):
                    label = item.get('label', 'Unknown')
                    atype = item.get('type', 'unknown')
                    icon = " "
                    if atype == 'submenu' or atype == 'generator':
                        icon = "+"
                    if atype == 'python':
                        icon = "►"
                    if atype == 'exit':
                        icon = "x"

                    print(f" {Colors.CYAN}{idx + 1}.{Colors.ENDC} [{icon}] {label}")

                if parent_label:
                    print(f" {Colors.CYAN}0.{Colors.ENDC} [<-] Back")

                print(f"\n{Colors.BLUE}──────────────────────────────────────────────────────────────────────────────────────────{Colors.ENDC}")
                
                prompt_text = f"{Colors.BOLD}Select option"
                if default_item:
                    prompt_text += " (or press Enter for Default)"
                prompt_text += f" > {Colors.ENDC}"
                choice = input(prompt_text)

                if not choice.strip():
                    if default_item:
                        self._execute_action(default_item)
                    else:
                        continue

                if choice == '0' and parent_label:
                    return

                if choice.isdigit():
                    idx = int(choice) - 1
                    if 0 <= idx < len(display_items):
                        self._execute_action(display_items[idx])

            except KeyboardInterrupt:
                print("\n")
                if parent_label:
                    return
                else:
                    Log.info("MENU: interrupted by user, exiting orchestrator")
                    print(f"{Colors.YELLOW}Interrupted by user. Cleaning up...{Colors.ENDC}")
                    try:
                        on_exit()
                    except Exception:
                        pass
                    sys.exit(0)

            except Exception as e:
                # Глобальный перехватчик ошибок самого меню
                Log.error(f"CRITICAL MENU ERROR: {e}")
                print(f"\n{Colors.RED}CRITICAL MENU ERROR: {e}{Colors.ENDC}")
                if self.is_debug:
                    traceback.print_exc()
                input("Press Enter to restart menu loop...")


if __name__ == "__main__":
    from pmi_logger import Log
    
    Log.info("PMI Orchestrator TUI started")
    os.environ["PMI_IS_ORCH"] = "1"

    builder = MenuBuilder()
    builder.run()