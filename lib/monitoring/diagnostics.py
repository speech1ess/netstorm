#!/usr/bin/env python3
import os
import sys
import socket
import subprocess
import time
import re
import xml.etree.ElementTree as ET
import py_compile
import yaml

try:
    from shared import Colors, SharedConfig
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import Colors, SharedConfig
    from pmi_logger import Log

try:
    from tools.target_manager import get_current_mode_string
except ImportError:
    get_current_mode_string = None

class SystemDiagnostics:
    def __init__(self):
        self.test_conf = SharedConfig.load_yaml('test_program.yaml')
        
        self.nodes = SharedConfig.get('nodes', {})
        self.global_paths = SharedConfig.get('paths', {})
        self.test_paths = self.test_conf.get('paths', {}) if self.test_conf else {}

    def _print_section(self, title):
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== {title} ==={Colors.ENDC}")

    def _print_sub(self, title):
        print(f"{Colors.BOLD}{Colors.BLUE}>> {title}{Colors.ENDC}")

    def _check_path_exists(self, label, path, is_file=False):
        if not path:
            print(f"  [{'FILE' if is_file else 'DIR'}] {label:<15} : {Colors.YELLOW}Not Defined{Colors.ENDC}")
            return False

        exists = os.path.isfile(path) if is_file else os.path.isdir(path)
        
        if exists:
            meta = ""
            if is_file:
                sz = os.path.getsize(path)
                meta = f"(Size: {sz}b)"
            else:
                try:
                    perm = oct(os.stat(path).st_mode)[-3:]
                    meta = f"(Perm: {perm})"
                except: pass
                
            print(f"  [{'FILE' if is_file else 'DIR'}] {label:<15} : {path:<35} {Colors.GREEN}OK{Colors.ENDC} {meta}")
            return True
        else:
            print(f"  [{'FILE' if is_file else 'DIR'}] {label:<15} : {path:<35} {Colors.RED}MISSING{Colors.ENDC}")
            return False

    def _validate_yaml(self, label, path):
        if not os.path.exists(path):
            return 

        try:
            with open(path, 'r') as f:
                yaml.safe_load(f)
            print(f"  [SYNTAX] {label:<13} : {Colors.GREEN}Valid YAML{Colors.ENDC}")
            return True
        except yaml.YAMLError as e:
            error_line = str(e).split('\n')[0]
            print(f"  [SYNTAX] {label:<13} : {Colors.RED}Invalid YAML{Colors.ENDC} -> {error_line}")
            Log.error(f"DIAG: YAML validation failed for {path}: {e}")
            return False

# ═══════════════════════════════════════════════════════════
    # 1. FILESYSTEM & CONFIG STRUCTURE
    # ═══════════════════════════════════════════════════════════
    def check_structure(self):
        Log.info("DIAG: starting filesystem & config check")
        self._print_section("1. FILESYSTEM & CONFIG CHECK")

        # 1.1 Config Files (Self-Check)
        self._print_sub("Configuration Files")
        base_dir = SharedConfig.get('paths.base', '/opt/pmi')
        config_dir = os.path.join(base_dir, 'config')
        
        global_path = os.path.join(config_dir, "global.yaml")
        test_path = os.path.join(config_dir, "test_program.yaml")
        menu_path = os.path.join(config_dir, "menu_structure.yaml")

        if self._check_path_exists("global.yaml", global_path, is_file=True):
            self._validate_yaml("global.yaml", global_path)
            
        if self._check_path_exists("test_program", test_path, is_file=True):
            self._validate_yaml("test_program.yaml", test_path)
            
        if self._check_path_exists("menu_structure", menu_path, is_file=True):
            self._validate_yaml("menu_structure.yaml", menu_path)
        
        # 1.2 Global Paths
        self._print_sub("System Paths (global.yaml)")
        for key, path in self.global_paths.items():
            if key in ['python', 'java']: continue
            self._check_path_exists(key, path)

        # 1.3 Methodology Overrides
        # Показываем только те пути из test_program, которые переопределяют глобальные
        overrides = {k: v for k, v in self.test_paths.items() if k not in self.global_paths or v != self.global_paths[k]}
        if overrides:
            self._print_sub("Methodology Overrides (test_program.yaml)")
            for key, path in overrides.items():
                self._check_path_exists(key, path)

    # ═══════════════════════════════════════════════════════════
    # 2. ATTACK PROFILES VALIDATION 
    # ═══════════════════════════════════════════════════════════
    def _find_profile(self, base_dir, tool_subfolder, filename):
        p1 = os.path.join(base_dir, filename)
        if os.path.exists(p1): return p1
        
        p2 = os.path.join(base_dir, tool_subfolder, filename)
        if os.path.exists(p2): return p2
        
        return None

    def check_profiles(self):
        if not self.test_conf:
            Log.warning("DIAG: skip profiles validation (no test_program.yaml)")
            return
        
        Log.info("DIAG: starting attack profiles validation")
        self._print_section("2. ATTACK PROFILES VALIDATION (From Current Config)")
        
        profiles_dir = self.test_paths.get('profiles')
        if not profiles_dir:
            base = self.global_paths.get('base', '/opt/pmi')
            profiles_dir = os.path.join(base, 'profiles')
            
        print(f"Search Root: {Colors.BOLD}{profiles_dir}{Colors.ENDC}")
        if not os.path.isdir(profiles_dir):
            Log.error(f"DIAG: profiles directory missing: {profiles_dir}")
            print(f"{Colors.RED}CRITICAL: Profiles directory missing!{Colors.ENDC}")
            return

        profiles = self.test_conf.get('profiles', {})

        # --- JMeter ---
        jmeter_profs = profiles.get('jmeter', {})
        if jmeter_profs:
            self._print_sub("JMeter Scenarios (.jmx)")
            for key, data in jmeter_profs.items():
                fname = data.get('jmx')
                fpath = self._find_profile(profiles_dir, 'jmeter', fname)
                
                if fpath:
                    try:
                        ET.parse(fpath)
                        valid = f"{Colors.GREEN}Valid XML{Colors.ENDC}"
                    except ET.ParseError:
                        valid = f"{Colors.RED}Invalid XML{Colors.ENDC}"
                    
                    rel_path = os.path.relpath(fpath, profiles_dir)
                    print(f"  [JMX] {key:<15} -> {rel_path:<30} {Colors.GREEN}FOUND{Colors.ENDC} | {valid}")
                else:
                    Log.warning(f"DIAG: JMeter profile '{key}' missing file '{fname}'")
                    print(f"  [JMX] {key:<15} -> {fname:<30} {Colors.RED}MISSING{Colors.ENDC} (checked subdirs)")

        # --- TRex ---
        trex_profs = profiles.get('trex', {})
        if trex_profs:
            self._print_sub("TRex Scripts (.py)")
            for key, data in trex_profs.items():
                fname = data.get('script')
                fpath = self._find_profile(profiles_dir, 'trex', fname)
                
                if fpath:
                    try:
                        py_compile.compile(fpath, cfile=None, doraise=True)
                        valid = f"{Colors.GREEN}Valid Py{Colors.ENDC}"
                    except py_compile.PyCompileError:
                        valid = f"{Colors.RED}Syntax Error{Colors.ENDC}"
                    
                    rel_path = os.path.relpath(fpath, profiles_dir)
                    print(f"  [PY]  {key:<15} -> {rel_path:<30} {Colors.GREEN}FOUND{Colors.ENDC} | {valid}")
                else:
                    Log.warning(f"DIAG: TRex profile '{key}' missing script '{fname}'")
                    print(f"  [PY]  {key:<15} -> {fname:<30} {Colors.RED}MISSING{Colors.ENDC} (checked subdirs)")

    # ═══════════════════════════════════════════════════════════
    # 3. BINARY & VERSION CHECKS
    # ═══════════════════════════════════════════════════════════
    def _get_bin_version(self, path):
        # Кастомный парсер для TRex
        if 't-rex-64' in path:
            try:
                out = subprocess.check_output([path, '--help'], stderr=subprocess.STDOUT, timeout=3).decode()
                match = re.search(r'Version\s*:\s*(v\d+\.\d+)', out)
                if match: return f"TRex {match.group(1)}"
                return "Detected (TRex)"
            except:
                return "Detected (Unknown Ver)"

        try:
            for flag in ['--version', '-v', '-version']:
                try:
                    out = subprocess.check_output([path, flag], stderr=subprocess.STDOUT, timeout=2).decode()
                    first_line = out.split('\n')[0].strip()
                    return first_line[:40]
                except subprocess.CalledProcessError:
                    continue
            return "Detected (Unknown Ver)"
        except:
            return "Detected"

    def check_local_binaries(self):
        Log.info("DIAG: starting local binaries & env check")
        self._print_section("3. LOCAL BINARIES & ENV")
        
        for key in ['python', 'java']:
            path = self.global_paths.get(key)
            if path and os.path.exists(path):
                 ver = self._get_bin_version(path)
                 print(f"  [ENV] {key:<10} : {path:<30} {Colors.GREEN}OK{Colors.ENDC} ({ver})")
            elif path:
                 Log.warning(f"DIAG: global binary '{key}' missing at {path}")
                 print(f"  [ENV] {key:<10} : {path:<30} {Colors.RED}MISSING{Colors.ENDC}")

        local_ips = ['127.0.0.1', 'localhost', '10.0.50.3'] 
        
        for node_key, node_data in self.nodes.items():
            ip = node_data.get('net', {}).get('ip')
            is_local = (ip in local_ips) or (node_key == 'trex_node') 
            
            if not is_local: continue

            procs = node_data.get('proc', {})
            for p_key, p_data in procs.items():
                if isinstance(p_data, dict):
                    bin_path = p_data.get('bin')
                    label = p_data.get('label', p_key)
                else: continue 

                if bin_path:
                    if os.path.exists(bin_path):
                        ver = self._get_bin_version(bin_path)
                        print(f"  [BIN] {label:<25} : {Colors.GREEN}FOUND{Colors.ENDC} -> {ver}")
                    else:
                        Log.warning(f"DIAG: service binary missing: {label} ({bin_path})")
                        print(f"  [BIN] {label:<25} : {Colors.RED}MISSING{Colors.ENDC} ({bin_path})")

    # ═══════════════════════════════════════════════════════════
    # 4. NETWORK & CONNECTIVITY
    # ═══════════════════════════════════════════════════════════
    def _check_endpoint(self, ip, port, proto, service_label, source_ns=None):
        if not ip or not port: return

        if proto.lower() not in ['http', 'https', 'tcp', 'zmq_async', 'zmq_sync', 'sshd', 'grafana', 'victoria_ui', 'victoria_api', 'nginx_http_exp', 'nginx_https_exp', 'nginx_http_status_exp']:
            return

        display_label = f"{ip}:{port}"
        try:
            if source_ns:
                cmd = ["ip", "netns", "exec", source_ns, "nc", "-z", "-w", "1", str(ip), str(port)]
                subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                print(f"  [PORT] {display_label:<21} : [From {source_ns}] {Colors.GREEN}OPEN{Colors.ENDC} ({service_label})")
            else:
                with socket.create_connection((ip, int(port)), timeout=1): pass
                print(f"  [PORT] {display_label:<21} : {Colors.GREEN}OPEN{Colors.ENDC} ({service_label})")
        except (socket.timeout, socket.error, FileNotFoundError, subprocess.CalledProcessError):
            prefix = f"[From {source_ns}] " if source_ns else ""
            print(f"  [PORT] {display_label:<21} : {prefix}{Colors.RED}CLOSED / TIMEOUT{Colors.ENDC} ({service_label})")
            Log.warning(f"DIAG: Port check failed for {display_label} ({service_label} from {source_ns or 'host'})")

    def _check_netns(self, ns_name, expected_ip, expected_iface):
        if not ns_name: return
        try:
            ns_list = subprocess.check_output(["ip", "netns", "list"]).decode()
        except:
            print(f"  [NS]  {ns_name:<15} : {Colors.RED}Check Failed (No Privileges?){Colors.ENDC}")
            return

        if ns_name not in ns_list:
            print(f"  [NS]  {ns_name:<15} : {Colors.RED}MISSING{Colors.ENDC}")
            return

        try:
            cmd = ["ip", "netns", "exec", ns_name, "ip", "-4", "-o", "addr", "show"]
            if expected_iface: cmd.append(expected_iface)
            
            out = subprocess.check_output(cmd).decode()
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out)
            real_ip = match.group(1) if match else "No IP"
            
            if expected_ip and real_ip == expected_ip:
                ip_status = f"{Colors.GREEN}{real_ip}{Colors.ENDC}"
            elif expected_ip:
                ip_status = f"{Colors.RED}{real_ip} (Exp: {expected_ip}){Colors.ENDC}"
            else:
                ip_status = f"{Colors.YELLOW}{real_ip}{Colors.ENDC}"
                
            print(f"  [NS]  {ns_name:<15} : {Colors.GREEN}UP{Colors.ENDC} | IP: {ip_status} | Dev: {expected_iface}")
        except subprocess.CalledProcessError:
             print(f"  [NS]  {ns_name:<15} : {Colors.YELLOW}UP (Empty/No Iface){Colors.ENDC}")

    def _smart_ping(self, target_ip, source_ns=None):
        if not target_ip: return "N/A"
        
        if source_ns:
            cmd = ["ip", "netns", "exec", source_ns, "ping", "-c", "1", "-W", "1", target_ip]
            prefix = f"[From {source_ns}]"
        else:
            cmd = ["ping", "-c", "1", "-W", "1", target_ip]
            prefix = "[Direct]"

        try:
            subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"{prefix} {Colors.GREEN}OK{Colors.ENDC}"
        except:
            return f"{prefix} {Colors.RED}FAIL{Colors.ENDC}"

    def check_network_nodes(self):
        Log.info("DIAG: starting network infrastructure check")
        self._print_section("4. NETWORK INFRASTRUCTURE & SERVICES")
        
        # === ПРОВЕРКА DUT ИЗ ПРОГРАММЫ ИСПЫТАНИЙ ===
        target_sec = self.test_conf.get('target', {})
        dut_ip = target_sec.get('dut_admin_ip')
        dut_desc = target_sec.get('description', 'DUT / NGFW')
        
        if dut_ip:
            self._print_sub(f"Active Target: {dut_desc}")
            res = self._smart_ping(dut_ip)
            print(f"  [PING] {dut_ip:<15} : {res}")

        # Умный поиск NetNS генератора (больше никакого хардкода vlan500!)
        jmeter_ns = self.nodes.get('jmeter_node', {}).get('net', {}).get('netns')

        for node_key, node_data in self.nodes.items():
            label = node_data.get('label', node_key)
            net = node_data.get('net', {})
            ip = net.get('ip')
            ns = net.get('netns')
            iface = net.get('iface')

            self._print_sub(f"Node: {label}")

            if node_key == 'victim' and get_current_mode_string:
                mode_str = get_current_mode_string()
                print(f"  [MODE] Target Status   : {mode_str}")

            if ns: self._check_netns(ns, ip, iface)

            # Вывод физических интерфейсов (специально для TRex)
            interfaces = net.get('interfaces', {})
            for iface_key, iface_data in interfaces.items():
                role = iface_data.get('role', 'unknown')
                net_ref = iface_data.get('network_ref', 'unknown')
                print(f"  [IFACE] {iface_key:<14} : {Colors.GREEN}CONFIGURED{Colors.ENDC} (role: {role}, net: {net_ref})")

            if not ip: continue
            
            # Эмуляция прохождения трафика: NGINX пингуем из NS генератора
            source_net = jmeter_ns if (node_key == 'victim' and jmeter_ns) else None
            
            if ip not in ['127.0.0.1', 'localhost']:
                res = self._smart_ping(ip, source_ns=source_net)
                print(f"  [PING] {ip:<15} : {res}")

            services = node_data.get('services', {})
            for svc_key, svc_data in services.items():
                port = svc_data.get('port')
                proto = svc_data.get('protocol', 'tcp')
                service_label = svc_data.get('label', svc_key)
                self._check_endpoint(ip, port, proto, service_label, source_ns=source_net)

    def run_all(self):
        Log.info("DIAG: System diagnostics started")
        print(f"{Colors.BOLD}Starting System diagnostics...{Colors.ENDC}")
        self.check_structure()
        self.check_profiles()
        self.check_local_binaries()
        self.check_network_nodes()
        Log.info("DIAG: System diagnostics completed")
        print(f"\n{Colors.BOLD}Diagnostics Completed.{Colors.ENDC}")

def run_all():
    diag = SystemDiagnostics()
    diag.run_all()

if __name__ == "__main__":
    run_all()