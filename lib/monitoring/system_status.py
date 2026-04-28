###/opt/pmi/lib/system_status.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys
import platform
import subprocess
import shutil
import re

# Пробуем подключить psutil для метрик железа
try:
    import psutil
except ImportError:
    psutil = None

try:
    from shared import SharedConfig, Colors
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import SharedConfig, Colors

class SystemStatus:
    def __init__(self):
        # 1. FIX: Получаем ноды через get(), чтобы не падало
        self.nodes = SharedConfig.get('nodes', {})
        self.logs_dir = SharedConfig.get('paths.logs', '/opt/pmi/logs')
       
        # 2. Собираем список сервисов (Твоя логика)
        self.services_list = []
        for node_key, node_val in self.nodes.items():
            procs = node_val.get('proc', {})
            for proc_key, proc_data in procs.items():
                if isinstance(proc_data, dict) and 'service_name' in proc_data:
                    self.services_list.append({
                        'name': proc_data['service_name'],
                        'label': proc_data.get('label', proc_key)
                    })

        # 3. Собираем список неймспейсов (Твоя логика)
        self.namespaces = {}
        for node_key, node_val in self.nodes.items():
            ns = node_val.get('net', {}).get('netns')
            if ns:
                self.namespaces[node_key] = ns

        # 4. IP Адреса для Trace (Твоя логика)
        self.target_ip = self.nodes.get('victim', {}).get('net', {}).get('ip', '127.0.0.1')
        self.dut_ip = self.nodes.get('dut', {}).get('net', {}).get('ip', None)

    # ════════════════════════════════════════════════════════
    # RESOURCE MONITORS (New)
    # ════════════════════════════════════════════════════════
    def _get_resources_line(self):
        if not psutil:
            return f"{Colors.CYAN}psutil not installed{Colors.ENDC}"
       
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
       
        # Цвет для CPU
        c_color = Colors.GREEN
        if cpu > 80: c_color = Colors.RED
        elif cpu > 50: c_color = Colors.YELLOW
       
        # Цвет для RAM
        m_color = Colors.GREEN
        if mem.percent > 90: m_color = Colors.RED
        elif mem.percent > 75: m_color = Colors.YELLOW

        return f"CPU: {c_color}{cpu}%{Colors.ENDC} | RAM: {m_color}{mem.used // (1024**2)}MB / {mem.total // (1024**2)}MB ({mem.percent}%){Colors.ENDC}"

    def _get_disk_line(self):
        try:
            total, used, free = shutil.disk_usage(self.logs_dir)
            free_gb = free / (1024**3)
            percent = (used / total) * 100
           
            d_color = Colors.GREEN
            if percent > 90: d_color = Colors.RED
            elif percent > 75: d_color = Colors.YELLOW
           
            return f"DISK: {d_color}{self.logs_dir} (Free: {free_gb:.1f} GB){Colors.ENDC}"
        except:
            return "DISK: N/A"

    # ════════════════════════════════════════════════════════
    # TOPOLOGY CHECKS (Preserved from your code)
    # ════════════════════════════════════════════════════════
    def _check_service(self, item):
        """Проверка Systemd сервиса"""
        name = item.get('name')
        label = item.get('label', name)
        
        # --- КАСТОМНАЯ ЛОГИКА ДЛЯ TREX ---
        if "trex" in name.lower():
            # Проверяем STL режим
            try:
                subprocess.check_call(["systemctl", "is-active", "--quiet", "trex-2.service"])
                return f"{Colors.GREEN}●{Colors.ENDC} {label} {Colors.CYAN}(STL){Colors.ENDC}"
            except subprocess.CalledProcessError:
                pass # Идем проверять ASTF
                
            # Проверяем ASTF режим
            try:
                subprocess.check_call(["systemctl", "is-active", "--quiet", "trex-2-astf.service"])
                return f"{Colors.GREEN}●{Colors.ENDC} {label} {Colors.CYAN}(ASTF){Colors.ENDC}"
            except subprocess.CalledProcessError:
                return f"{Colors.RED}●{Colors.ENDC} {label} (DOWN)"
            except FileNotFoundError:
                return f"{Colors.YELLOW}?{Colors.ENDC} {label}"
        # ----------------------------------

        # СТАНДАРТНАЯ ЛОГИКА ДЛЯ ОСТАЛЬНЫХ
        try:
            subprocess.check_call(["systemctl", "is-active", "--quiet", name])
            return f"{Colors.GREEN}●{Colors.ENDC} {label}"
        except subprocess.CalledProcessError:
            return f"{Colors.RED}●{Colors.ENDC} {label}"
        except FileNotFoundError:
            return f"{Colors.YELLOW}?{Colors.ENDC} {label}"

    def _get_ns_ip(self, ns):
        try:
            cmd = ["ip", "netns", "exec", ns, "ip", "-4", "addr", "show"]
            output = subprocess.check_output(cmd).decode()
            match = re.search(r"inet (?!127)(\d+\.\d+\.\d+\.\d+)", output)
            return match.group(1) if match else "?"
        except:
            return "?"

    def _get_ns_iface(self, ns):
        try:
            cmd = ["ip", "netns", "exec", ns, "ip", "-o", "link", "show"]
            output = subprocess.check_output(cmd).decode()
            for line in output.split('\n'):
                if ": lo:" in line or not line: continue
                parts = line.split(': ')
                if len(parts) >= 2:
                    return parts[1].split('@')[0].strip()
            return "?"
        except:
            return "?"

    def _check_netns_item(self, node_key, ns_name):
        try:
            active_ns = subprocess.check_output(["ip", "netns", "list"]).decode()
            found = False
            for line in active_ns.split('\n'):
                if line.startswith(ns_name):
                    found = True
                    break
           
            if found:
                ip = self._get_ns_ip(ns_name)
                iface = self._get_ns_iface(ns_name)
                return f"{node_key}: {Colors.GREEN}UP{Colors.ENDC} ({Colors.CYAN}{iface}{Colors.ENDC}: {ip})"
            else:
                return f"{node_key}: {Colors.RED}DOWN{Colors.ENDC}"
        except:
            return f"{node_key}: {Colors.RED}ERR{Colors.ENDC}"

    def _get_ns_gateway(self, ns):
        try:
            cmd = ["ip", "netns", "exec", ns, "ip", "route", "show", "default"]
            output = subprocess.check_output(cmd).decode()
            match = re.search(r"via (\d+\.\d+\.\d+\.\d+)", output)
            return match.group(1) if match else None
        except:
            return None

    def _ping_host(self, ns, target):
        if not target: return False
        try:
            cmd = ["ip", "netns", "exec", ns, "ping", "-c", "1", "-W", "0.2", target]
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except:
            return False

    def _trace_visual(self):
        src_ns = self.namespaces.get('jmeter_node')
        if not src_ns and self.namespaces:
             src_ns = next(iter(self.namespaces.values()))
       
        if not src_ns: return f"{Colors.YELLOW}Trace Skipped (No NS){Colors.ENDC}"

        gw_ip = self._get_ns_gateway(src_ns)
        chain = f"{Colors.BOLD}GEN{Colors.ENDC}"

        if gw_ip:
            status = f"{Colors.GREEN}─✓─>{Colors.ENDC}" if self._ping_host(src_ns, gw_ip) else f"{Colors.RED}─✗─>{Colors.ENDC}"
            chain += f" {status} {Colors.BOLD}GW{Colors.ENDC}"
        else:
            chain += f" {Colors.YELLOW}─?─>{Colors.ENDC} {Colors.BOLD}GW{Colors.ENDC}"

        if self.dut_ip:
            status = f"{Colors.GREEN}─✓─>{Colors.ENDC}" if self._ping_host(src_ns, self.dut_ip) else f"{Colors.RED}─✗─>{Colors.ENDC}"
            chain += f" {status} {Colors.BOLD}DUT{Colors.ENDC}"
       
        if self._ping_host(src_ns, self.target_ip):
             chain += f" {Colors.GREEN}─✓─>{Colors.ENDC} {Colors.GREEN}TARGET{Colors.ENDC}"
        else:
             chain += f" {Colors.RED}─✗─>{Colors.ENDC} {Colors.RED}TARGET{Colors.ENDC}"
       
        chain += f" ({self.target_ip})"
        return chain

    def _format_multiline(self, title, items, chunk_size):
        if not items: return f" {title} None"
        lines = []
        indent = " " * len(title)
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            line_str = " | ".join(chunk)
            if i == 0: lines.append(f" {title} {line_str}")
            else: lines.append(f" {indent} {line_str}")
        return "\n".join(lines)

    # ════════════════════════════════════════════════════════
    # MAIN DASHBOARD BUILDER
    # ════════════════════════════════════════════════════════
    def get_dashboard(self):
        # 1. Info Header
        uname = platform.node()
        res_line = self._get_resources_line()
        disk_line = self._get_disk_line()
       
        # Session info
        run_id = os.environ.get("PMI_RUN_ID", "NONE")
        if run_id != "NONE":
            sess_str = f"SESSION: {Colors.GREEN}{Colors.BOLD}{run_id}{Colors.ENDC}"
        else:
            sess_str = f"SESSION: {Colors.CYAN}IDLE{Colors.ENDC}"

        # 2. Topology Checks
        svc_items = [self._check_service(item) for item in self.services_list]
        svc_str = self._format_multiline(f"{Colors.BOLD}[Services]{Colors.ENDC}  ", svc_items, 3)

        netns_items = []
        for node_key, ns_name in self.namespaces.items():
            netns_items.append(self._check_netns_item(node_key, ns_name))
        netns_str = self._format_multiline(f"{Colors.BOLD}[NetNS]{Colors.ENDC}     ", netns_items, 2)

        trace_str = f" {Colors.BOLD}[Trace]{Colors.ENDC}     {self._trace_visual()}"

        sep = f"{Colors.BLUE}──────────────────────────────────────────────────────────────────────────────────────────{Colors.ENDC}"
       
        return "\n".join([
            sep,
            f" {Colors.BOLD}HOST:{Colors.ENDC} {uname}   {res_line}",
            f" {disk_line}   {sess_str}",
            sep,
            svc_str,
            netns_str,
            f"{Colors.CYAN} - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -{Colors.ENDC}",
            trace_str,
            sep
        ])

if __name__ == "__main__":
    print(SystemStatus().get_dashboard())

