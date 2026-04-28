import os
import sys
import subprocess
import time

# === ИМПОРТЫ ИЗ SHARED ===
try:
    from shared import Colors, SharedConfig, SharedTrap, on_exit
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import Colors, SharedConfig, SharedTrap, on_exit
    from pmi_logger import Log

# === НАСТРОЙКИ ===
NGINX_AVAIL = "/etc/nginx/sites-available"
NGINX_ENABLED = "/etc/nginx/sites-enabled/default"

# Настройки фермы (вместо backend.sh)
WORKERS = 24
BASE_PORT = 8100
TARGET_MONITOR_SCRIPT = "/opt/pmi/lib/monitoring/system_monitor.py"
NETNS = SharedConfig.get('nodes.victim.net.netns', 'webserver') # Динамический неймспейс!

# Имена файлов конфигов
CONF_BACKEND = "backend"   
CONF_STATIC = "loadtest"   

def _run_cmd(cmd, check=False):
    """Helper to run shell commands with logging."""
    Log.debug(f"TARGET_MAN: Running cmd: '{cmd}'")
    try:
        proc = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=check)
        return proc
    except subprocess.CalledProcessError as e:
        Log.error(f"Command failed: {e.cmd}\nStderr: {e.stderr}")
        return e

def switch_nginx(config_name):
    src = os.path.join(NGINX_AVAIL, config_name)
    
    if not os.path.exists(src):
        Log.error(f"Config file not found: {src}")
        return False

    try:
        if os.path.exists(NGINX_ENABLED) or os.path.islink(NGINX_ENABLED):
            os.remove(NGINX_ENABLED)
        
        os.symlink(src, NGINX_ENABLED)
        Log.info(f"Symlink updated to: {Colors.CYAN}{config_name}{Colors.ENDC}")
        
        Log.info("Reloading Nginx...")
        res = _run_cmd("service nginx reload")
        
        if res.returncode != 0:
            Log.error(f"Nginx Reload Failed:\n{res.stderr}")
            return False
            
        return True
    except Exception as e:
        Log.error(f"Exception during Nginx switch: {e}")
        return False

# === ЛОГИКА ВОРКЕРОВ (УБИЛИ BACKEND.SH) ===
def stop_workers():
    """Убивает ферму Python"""
    _run_cmd(f"pkill -f 'python3 {TARGET_MONITOR_SCRIPT}'")

def start_workers():
    """Запускает ферму Python"""
    stop_workers() # На всякий случай зачищаем
    _run_cmd(f"ip netns exec {NETNS} ip link set lo up") # Важно!
    
    for i in range(1, WORKERS + 1):
        port = BASE_PORT + i
        cmd = f"ip netns exec {NETNS} python3 {TARGET_MONITOR_SCRIPT} {port}"
        # Запускаем асинхронно в фоне
        subprocess.Popen(
            cmd, 
            shell=True, 
            stdout=subprocess.DEVNULL, 
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )

# === ОСНОВНЫЕ ФУНКЦИИ МЕНЮ ===
def switch_to_backend():
    print(f"\n{Colors.MAGENTA}{Colors.BOLD}=== ACTIVATING BACKEND MODE (CPU STRESS) ==={Colors.ENDC}")
    Log.info("Switching to Python Backend cluster...")
    
    if not switch_nginx(CONF_BACKEND):
        input(f"\n{Colors.RED}Press Enter to return (Error)...{Colors.ENDC}")
        return

    print(f"{Colors.YELLOW}Starting Python Workers inside netns '{NETNS}'...{Colors.ENDC}")
    start_workers()
    Log.success("Python Farm started successfully.")

    time.sleep(1)
    show_status()
    if os.environ.get("PMI_WEB_MODE") != "1":
        input(f"\n{Colors.BOLD}Press Enter to return...{Colors.ENDC}")

def switch_to_static():
    print(f"\n{Colors.MAGENTA}{Colors.BOLD}=== ACTIVATING STATIC MODE (NETWORK STRESS) ==={Colors.ENDC}")
    Log.info("Switching to Static Loadtest mode...")

    print(f"{Colors.YELLOW}Stopping Python Workers...{Colors.ENDC}")
    stop_workers()
    Log.info("Python Workers stopped.")

    if switch_nginx(CONF_STATIC):
        Log.success("Switched to Static Mode.")

    time.sleep(1)
    show_status()
    if os.environ.get("PMI_WEB_MODE") != "1":
        input(f"\n{Colors.BOLD}Press Enter to return...{Colors.ENDC}")

def get_current_mode_string():
    """Returns a colorized string of the current Nginx mode."""
    if os.path.exists(NGINX_ENABLED):
        try:
            real_path = os.path.realpath(NGINX_ENABLED)
            if CONF_BACKEND in real_path:
                return f"{Colors.YELLOW}BACKEND (Python CPU){Colors.ENDC}"
            elif CONF_STATIC in real_path:
                return f"{Colors.BLUE}STATIC (Nginx Native){Colors.ENDC}"
        except Exception:
            return f"{Colors.RED}UNKNOWN (Error reading link){Colors.ENDC}"
    return f"{Colors.RED}UNKNOWN (No config enabled){Colors.ENDC}"

def show_status():
    print(f"\n{Colors.BOLD}--- Target Server Status ---{Colors.ENDC}")
    
    current_mode = get_current_mode_string()
    print(f"Current Mode: {current_mode}")

    # Считаем питонов (теперь ищем конкретный скрипт)
    py_count = int(subprocess.getoutput(f"pgrep -f 'python3 {TARGET_MONITOR_SCRIPT}' | wc -l"))
    
    if py_count > 0:
        print(f"Active Python Workers: {Colors.GREEN}{py_count}{Colors.ENDC}")
    else:
        print(f"Active Python Workers: {Colors.BOLD}0 (Stopped){Colors.ENDC}")
    
    # Health Check с динамическим неймспейсом
    try:
        check = subprocess.getoutput(f"ip netns exec {NETNS} curl -s -o /dev/null -w '%{{http_code}}' localhost")
        
        if check == "200":
            print(f"Health Check (inside {NETNS}): [{Colors.GREEN} 200 OK {Colors.ENDC}]")
        elif check == "000":
             print(f"Health Check (inside {NETNS}): [{Colors.RED} FAIL (Refused) {Colors.ENDC}]")
        else:
            print(f"Health Check (inside {NETNS}): [{Colors.RED} {check} {Colors.ENDC}]")
    except Exception as e:
        print(f"Health Check Error: {e}")

if __name__ == "__main__":
    show_status()