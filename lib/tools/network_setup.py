#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import ipaddress
import random
import re

try:
    from shared import Colors, SharedConfig
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared import Colors, SharedConfig
    from pmi_logger import Log

class NetworkSetup:
    def __init__(self):
        self.profiles_dir = SharedConfig.get('paths.profiles', '/opt/pmi/profiles')
        self.base_csv = os.path.join(self.profiles_dir, 'jmeter', 'source_ips.csv')
        self.attack_csv = os.path.join(self.profiles_dir, 'jmeter', 'attack_ips.csv')
        
        self.ns_name = SharedConfig.get('nodes.jmeter_node.net.netns')
        self.legit_pool = SharedConfig.get('networks.external.subnets.jmeter_base', '172.17.0.0/16')
        self.attack_pool = SharedConfig.get('networks.external.subnets.jmeter_attack', '10.17.0.0/16')

    @staticmethod
    def _run(cmd):
        return subprocess.run(cmd, shell=True, capture_output=True, text=True)

    def _get_subnet_capacity(self, cidr):
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            return max(0, net.num_addresses - 2)
        except:
            return 0

    def _count_lines(self, filepath):
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return 0
        try:
            with open(filepath, 'r') as f:
                return sum(1 for _ in f)
        except:
            return 0

    def _generate_csv(self, cidr, output_path, max_count=60000):
        try:
            net = ipaddress.ip_network(cidr, strict=False)
            num_hosts = net.num_addresses - 2
            count = min(max_count, num_hosts)
            
            Log.info(f"Generating {count} IPs from {cidr}...")
            sampled = random.sample(range(1, num_hosts + 1), count)
            
            with open(output_path, 'w') as f:
                for idx in sampled:
                    f.write(f"{net.network_address + idx}\n")
            return count
        except Exception as e:
            Log.error(f"Generation failed for {cidr}: {e}")
            return 0

    # === НОВЫЕ ХЕЛПЕРЫ ДЛЯ ПЕРЕИСПОЛЬЗОВАНИЯ ===

    def _check_netns(self, ns_name, expected_ip, expected_iface):
        """Честная проверка неймспейса (UP/DOWN, сверка IP-адреса)"""
        if not ns_name: return
        try:
            ns_list = self._run("ip netns list").stdout
        except:
            print(f"  [NS]  {ns_name:<15} : {Colors.RED}Check Failed{Colors.ENDC}  {Colors.RED}[❌ FAIL]{Colors.ENDC}")
            return

        if ns_name not in ns_list:
            print(f"  [NS]  {ns_name:<15} : {Colors.RED}MISSING{Colors.ENDC}  {Colors.RED}[❌ FAIL]{Colors.ENDC}")
            return

        try:
            cmd = f"ip netns exec {ns_name} ip -4 -o addr show"
            if expected_iface and expected_iface != "unknown":
                cmd += f" dev {expected_iface}"
            
            out = self._run(cmd).stdout
            match = re.search(r'inet\s+(\d+\.\d+\.\d+\.\d+)', out)
            real_ip = match.group(1) if match else "No IP"
            
            exp_ip = expected_ip.split('/')[0] if expected_ip else ""
            
            is_ok = True
            if exp_ip and real_ip == exp_ip:
                ip_status = f"{Colors.GREEN}{real_ip}{Colors.ENDC}"
            elif exp_ip:
                ip_status = f"{Colors.RED}{real_ip} (Exp: {exp_ip}){Colors.ENDC}"
                is_ok = False
            else:
                ip_status = f"{Colors.YELLOW}{real_ip}{Colors.ENDC}"
                
            status_icon = f" {Colors.GREEN}[✓ OK]{Colors.ENDC}" if is_ok else f" {Colors.RED}[❌ FAIL]{Colors.ENDC}"
            
            # Выравниваем вывод Dev, чтобы галочки стояли ровно
            print(f"  [NS]  {ns_name:<15} : {Colors.GREEN}UP{Colors.ENDC} | IP: {ip_status:<22} | Dev: {expected_iface:<10} {status_icon}")
        except Exception:
            print(f"  [NS]  {ns_name:<15} : {Colors.YELLOW}UP (Empty/No Iface){Colors.ENDC}  {Colors.YELLOW}[⚠ WARN]{Colors.ENDC}")

    def _print_pool_stats(self):
        """Хелпер для сбора и вывода статистики по пулам (CSVs, Interfaces)"""
        res = self._run(f"ip netns exec {self.ns_name} ip -o link show | awk -F': ' '$2!= \"lo\" {{print $2}}' | head -n 1")
        iface = res.stdout.strip()
        if not iface:
            return 0, 0, 0, None

        base_lines = self._count_lines(self.base_csv)
        attack_lines = self._count_lines(self.attack_csv)
        base_cap = self._get_subnet_capacity(self.legit_pool)
        attack_cap = self._get_subnet_capacity(self.attack_pool)
        
        res_ips = self._run(f"ip netns exec {self.ns_name} ip addr show dev {iface} | grep -c 'inet '")
        current_ips = int(res_ips.stdout.strip() or 0)

        # print(f"  Target NetNS     : {Colors.BOLD}{self.ns_name}{Colors.ENDC} (Iface: {iface})")
        print(f"    IPs on Interface : {Colors.BOLD}{current_ips}{Colors.ENDC}")
        
        print(f"    [{Colors.BLUE}Baseline Pool{Colors.ENDC}] Config: {self.legit_pool} (Max: {base_cap}) | CSV: {base_lines} IPs")
        print(f"    [{Colors.RED}Attack Pool{Colors.ENDC}]   Config: {self.attack_pool} (Max: {attack_cap}) | CSV: {attack_lines} IPs")
        
        return base_lines, attack_lines, current_ips, iface

    # ==========================================

    def provision_jmeter_ips(self):
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== JMeter IP Provisioning ==={Colors.ENDC}")
        
        if not self.ns_name:
            print(f"{Colors.RED}Error: 'jmeter_node' netns not found in config!{Colors.ENDC}")
            return False

        # Используем хелпер для статистики
        stats = self._print_pool_stats()
        if not stats[3]:
            print(f"{Colors.RED}Error: No interface found in namespace {self.ns_name}{Colors.ENDC}")
            return False
            
        base_lines, attack_lines, current_ips, iface = stats

        # 3. Логика принятия решений
        regenerate = False
        if base_lines == 0 and attack_lines == 0:
            print(f"{Colors.YELLOW}CSV files missing or empty. Auto-generating...{Colors.ENDC}")
            regenerate = True
        else:
            print(f"{Colors.GREEN}CSVs already exist. Using existing pools for provisioning.{Colors.ENDC}\n")

        if regenerate:
            print("Generating new CSV files...")
            base_lines = self._generate_csv(self.legit_pool, self.base_csv)
            attack_lines = self._generate_csv(self.attack_pool, self.attack_csv)
            print(f"{Colors.GREEN}Generation complete.{Colors.ENDC}\n")

        if base_lines == 0 and attack_lines == 0:
            print(f"{Colors.RED}No IPs to provision. Exiting.{Colors.ENDC}")
            return False

        # === ЗАЩИТА ОТ КАМИКАДЗЕ ===
        if current_ips > 5:
            print(f"{Colors.YELLOW}Interface {iface} already has {current_ips} IPs.{Colors.ENDC}")
            print(f"{Colors.GREEN}Provisioning skipped (already provisioned).{Colors.ENDC}")
            print(f"To rebuild pools, run 'Destroy JMeter NetNS (Teardown)' first.\n")
            return True
        # ===========================

        # 4. Формируем Batch-файл
        tmp_batch = "/tmp/ip_batch_cmd.txt"
        print("Merging IP pools into batch file...")
        
        base_prefix = self.legit_pool.split('/')[-1] if '/' in self.legit_pool else '16'
        attack_prefix = self.attack_pool.split('/')[-1] if '/' in self.attack_pool else '16'

        with open(tmp_batch, 'w') as out_f:
            for csv_file, prefix in [(self.base_csv, base_prefix), (self.attack_csv, attack_prefix)]:
                if os.path.exists(csv_file):
                    with open(csv_file, 'r') as in_f:
                        for line in in_f:
                            ip = line.strip()
                            if ip: out_f.write(f"addr add {ip}/{prefix} dev {iface}\n")

        # 5. МАГИЯ ПРОВИЖЕНИНГА (Секретный прием с DOWN/UP)
        print("Applying sysctl tuning (ARP)...")
        self._run("sysctl -w net.ipv4.neigh.default.gc_thresh1=32768 > /dev/null")
        self._run("sysctl -w net.ipv4.neigh.default.gc_thresh2=49152 > /dev/null")
        self._run("sysctl -w net.ipv4.neigh.default.gc_thresh3=65536 > /dev/null")

        print(f"Bringing interface {Colors.RED}DOWN{Colors.ENDC} for ultra-fast provisioning...")
        self._run(f"ip netns exec {self.ns_name} ip link set {iface} down")

        print("Applying massive IP pool...")
        start_time = time.time()
        self._run(f"ip netns exec {self.ns_name} ip -batch {tmp_batch}")
        
        print(f"Bringing interface {Colors.GREEN}UP{Colors.ENDC}...")
        self._run(f"ip netns exec {self.ns_name} ip link set {iface} up")
        
        if os.path.exists(tmp_batch):
            os.remove(tmp_batch)

        # 6. Тюнинг NetNS (делаем после UP на всякий случай)
        self._run(f"ip netns exec {self.ns_name} sysctl -w net.ipv4.conf.all.rp_filter=2 > /dev/null")
        self._run(f"ip netns exec {self.ns_name} sysctl -w net.ipv4.conf.{iface}.rp_filter=2 > /dev/null")
        self._run(f"ip netns exec {self.ns_name} sysctl -w net.ipv4.ip_local_port_range=\"1024 65535\" > /dev/null")
        self._run(f"ip netns exec {self.ns_name} sysctl -w net.ipv4.tcp_tw_reuse=1 > /dev/null")
        self._run(f"ip netns exec {self.ns_name} sysctl -w net.ipv4.tcp_fin_timeout=15 > /dev/null")

        # 7. Проверка результата
        res_ips = self._run(f"ip netns exec {self.ns_name} ip addr show dev {iface} | grep -c 'inet '")
        final_ips = res_ips.stdout.strip()
        elapsed = round(time.time() - start_time, 2)
        
        print(f"\n{Colors.GREEN}Done! Total IPs on {iface}: {final_ips} (Provisioning took {elapsed}s){Colors.ENDC}\n")
        return True

    def setup_jmeter_ns(self):
        """Эквивалент init_jmeter_ns.sh (start)"""
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== Setup JMeter NetNS ==={Colors.ENDC}")
        
        ns_name = self.ns_name
        net_cfg = SharedConfig.get('nodes.jmeter_node.net', {})
        iface = net_cfg.get('iface')
        ip_addr = net_cfg.get('ip')     # Желательно с маской, например 10.0.50.4/29
        gw = net_cfg.get('gw')          # Шлюз, например 10.0.50.2
        
        if not all([ns_name, iface, ip_addr, gw]):
            print(f"{Colors.RED}Error: Missing netns, iface, ip, or gw in config for jmeter_node!{Colors.ENDC}")
            return False

        print(f"Creating namespace {Colors.BOLD}{ns_name}{Colors.ENDC}...")
        self._run(f"ip netns add {ns_name}")
        self._run(f"ip netns exec {ns_name} ip link set lo up")

        print(f"Moving physical interface {iface} to {ns_name}...")
        self._run(f"ip link set {iface} netns {ns_name}")

        print("Configuring address and routes...")
        # Если в конфиге IP без маски, добавим /29 по умолчанию
        if '/' not in ip_addr: ip_addr += '/29'
        
        self._run(f"ip netns exec {ns_name} ip addr add {ip_addr} dev {iface}")
        self._run(f"ip netns exec {ns_name} ip link set {iface} up")
        self._run(f"ip netns exec {ns_name} ip route add default via {gw} dev {iface}")

        print(f"{Colors.GREEN}JMeter Network Namespace READY.{Colors.ENDC}")

    def teardown_jmeter_ns(self):
        """Эквивалент init_jmeter_ns.sh (stop)"""
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== Teardown JMeter NetNS ==={Colors.ENDC}")
        
        if not self.ns_name: return False
        iface = SharedConfig.get('nodes.jmeter_node.net.iface')

        print(f"Moving interface {iface} back to host...")
        self._run(f"ip netns exec {self.ns_name} ip link set {iface} netns 1")
        
        print(f"Deleting namespace {self.ns_name}...")
        self._run(f"ip netns del {self.ns_name}")
        
        print(f"{Colors.GREEN}Namespace destroyed successfully.{Colors.ENDC}")

    def show_routing_cheatsheet(self):
        """Умная генерация шпаргалки по маршрутам на основе полной топологии стенда"""
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== 🗺️  Test Stand Topology  ==={Colors.ENDC}\n")

        # --- 1. ДОСТАЕМ ДАННЫЕ ИЗ ИНФРАСТРУКТУРЫ (global.yaml) ---
        sw_ext_label = SharedConfig.get('networks.external.gateway.label', 'EXTERNAL SWITCH')
        sw_int_label = SharedConfig.get('networks.internal.gateway.label', 'INTERNAL SWITCH')

        vlan_ext = SharedConfig.get('networks.external.vlan', '500')
        vlan_int = SharedConfig.get('networks.internal.vlan', '400')
        
        net_ext = SharedConfig.get('networks.external.subnets.cidr', '10.0.50.0/29')
        net_int = SharedConfig.get('networks.internal.subnets.cidr', '10.0.40.0/29')
        net_sf = SharedConfig.get('networks.server_farm.subnets.cidr', '10.0.70.0/29')

        pool_jmeter_base = SharedConfig.get('networks.external.subnets.jmeter_base', '172.17.0.0/16')
        pool_jmeter_atk = SharedConfig.get('networks.external.subnets.jmeter_attack', '10.17.0.0/16')
        pool_trex_clients = SharedConfig.get('networks.external.subnets.trex_clients', '15.0.0.0/13')
        pool_trex_servers = SharedConfig.get('networks.server_farm.subnets.trex_servers', '47.0.0.0/26')

        ip_jmeter = SharedConfig.get('nodes.jmeter_node.net.ip', '10.0.50.4/29').split('/')[0]
        ip_trex_ext = SharedConfig.get('nodes.trex_node.net.interfaces.port_0.addr', '10.0.50.3')
        ip_trex_int = SharedConfig.get('nodes.trex_node.net.interfaces.port_1.addr', '10.0.70.3') 

        gw_ext = SharedConfig.get('networks.external.gateway.addr', '10.0.50.2')
        gw_dmz = SharedConfig.get('networks.dmz.gateway.addr', '10.0.60.2')
        gw_int = SharedConfig.get('networks.internal.gateway.addr', '10.0.40.2')
        gw_sf = SharedConfig.get('networks.server_farm.gateway.addr')

        # --- 2. ДОСТАЕМ ДАННЫЕ ИЗ МЕТОДИКИ (test_program.yaml) ---
        try:
            test_prog = SharedConfig.load_yaml('test_program.yaml')
            # Безопасное извлечение вложенных ключей
            dut_label = test_prog.get('program', {}).get('dut', {}).get('label', 'Unknown DUT')
            dut_ext_ip = test_prog.get('program', {}).get('dut', {}).get('datapath', {}).get('ext_ip', '10.0.60.5')
            dut_int_ip = test_prog.get('program', {}).get('dut', {}).get('datapath', {}).get('int_ip', '10.0.40.5')
        except Exception as e:
            Log.error(f"Failed to load test_program.yaml: {e}")
            dut_label = "Device Under Test (Fallback)"
            dut_ext_ip = "XX.XX.XX.XX"
            dut_int_ip = "ZZ.ZZ.ZZ.ZZ"

        sw_ext_short = sw_ext_label.split(' ')[0]
        sw_int_short = sw_int_label.split(' ')[0]

        # --- 3. ГЕНЕРИРУЕМ ВЫВОД ---
        # --- ТОПОЛОГИЯ И РЕЖИМ РАБОТЫ ---
        print(f"{Colors.DIM}# ПРИМЕЧАНИЕ: Схема и маршруты ниже описаны для интеграции на уровне L3 (DUT в режиме Router).{Colors.ENDC}")
        print(f"{Colors.DIM}# Для интеграции в сеть на уровне L2 (DUT режиме Bridge), нужно заменить Next-Hop{Colors.ENDC}")
        print(f"{Colors.DIM}# с адресов DUT ({dut_ext_ip} / {dut_int_ip}) на адрес шлюза DMZ ({gw_dmz}), а также{Colors.ENDC}")
        print(f"{Colors.DIM}# обеспечить сетевую связность сегментов внешней ({net_ext}) и внутренней ({net_int}) сетей.{Colors.ENDC}\n")

        print("      [ Traffic Generators ]")
        print("                │")
        print(f"                │ {pool_jmeter_base}")
        print(f"                │ {pool_jmeter_atk}")
        print(f"                │ {pool_trex_clients}")
        print(f"                │ via {gw_ext}")
        print(f"                │ --------")
        print(f"                │ {net_ext}")
        print("                ▼")
        print("       ┌──────────────────┐")
        print(f"       │  {sw_ext_short:<15} │ (VLAN {vlan_ext})")
        print("       └────────┬─────────┘")
        print(f"                │ {dut_ext_ip}")
        print("                ▼")
        print("       ╔══════════════════╗")
        print(f"       ║  {dut_label[:14]:<14}..║ (DUT)")
        print("       ╚════════┬═════════╝")
        print(f"                │ {gw_int}")
        print("                ▼")
        print("       ┌──────────────────┐")
        print(f"       │  {sw_int_short:<15} │ (VLAN {vlan_int})")
        print("       └────────┬─────────┘")
        print("                │")
        print(f"                │ {net_int}")
        print(f"                │ --------")
        print(f"                │ {net_sf} via {gw_sf}")
        print(f"                │ {pool_trex_servers} via {ip_trex_int}")
        print("                ▼")
        print("           [ Targets ]\n")

        print("===========================================================================")
        print(" ИНФРАСТРУКТУРА (global.yaml -> networks)")
        print("===========================================================================\n")

        print(f"[ 🌐 EXTERNAL ZONE: {sw_ext_short} | VLAN {vlan_ext} | {net_ext} ]")
        print(f"{Colors.DIM}# 1. Прямой маршрут (Forward path) — трафик в сторону целей:{Colors.ENDC}")
        print(f"ip route add {net_int} via {dut_ext_ip}   {Colors.GREEN}# -> NGINX Target subnet via DUT (Ext IP){Colors.ENDC}")
        print(f"ip route add {net_sf} via {dut_ext_ip}   {Colors.GREEN}# -> Server Farm subnet via DUT (Ext IP){Colors.ENDC}")
        print(f"ip route add {pool_trex_servers} via {dut_ext_ip}   {Colors.GREEN}# -> TRex Server Pool via DUT (Ext IP){Colors.ENDC}")
        
        print(f"\n{Colors.DIM}# 2. Обратные маршруты (Return path) в виртуальные подсети:{Colors.ENDC}")
        print(f"ip route add {pool_jmeter_base} via {ip_jmeter}  {Colors.BLUE}# -> JMeter (Baseline Pool){Colors.ENDC}")
        print(f"ip route add {pool_jmeter_atk} via {ip_jmeter}  {Colors.BLUE}# -> JMeter (Attack Pool){Colors.ENDC}")
        print(f"ip route add {pool_trex_clients} via {ip_trex_ext}  {Colors.RED}# -> TRex Port 0 (Client Pool){Colors.ENDC}\n")


        print(f"[ 🏢 INTERNAL ZONE: {sw_int_short} | VLAN {vlan_int} | {net_int} ]")
        print(f"{Colors.DIM}# 1. Прямой маршрут (Forward path) — до эмулируемых бэкендов TRex:{Colors.ENDC}")
        print(f"ip route add {pool_trex_servers} via {ip_trex_int}    {Colors.RED}# -> TRex Port 1 (Server Farm Pool){Colors.ENDC}")
        
        print(f"\n{Colors.DIM}# 2. Обратные маршруты (Return path) — симметрия трафика через DUT:{Colors.ENDC}")
        print(f"ip route add {net_ext} via {dut_int_ip}  {Colors.BLUE}# -> External subnet (MGMT/Direct){Colors.ENDC}")
        print(f"ip route add {pool_jmeter_base} via {dut_int_ip}  {Colors.BLUE}# -> JMeter (Baseline Pool){Colors.ENDC}")
        print(f"ip route add {pool_jmeter_atk} via {dut_int_ip}  {Colors.BLUE}# -> JMeter (Attack Pool){Colors.ENDC}")
        print(f"ip route add {pool_trex_clients} via {dut_int_ip}  {Colors.RED}# -> TRex Port 0 (Client Pool){Colors.ENDC}\n")


        print("===========================================================================")
        print(" DUT (test_program.yaml -> program.dut)*")
        print("===========================================================================")
        print(f"{Colors.DIM}* Внимание: Данные маршруты зависят от программы тестирования{Colors.ENDC}\n")

        print(f"[ 🛡️  DUT: {dut_label} ]")
        print(f"{Colors.DIM}# Базовые статические маршруты для режима L3 (Inline):{Colors.ENDC}")
        print(f"ip route add 0.0.0.0/0 via {gw_dmz}      {Colors.DIM}# Default маршрут во внешнюю сеть (на {sw_ext_short}){Colors.ENDC}")
        print(f"ip route add {net_int} via {gw_int}   {Colors.GREEN}# Объекты защиты (на {sw_int_short}){Colors.ENDC}\n")
        
        # --- 4. АКТИВНЫЕ ПРОВЕРКИ ЛОКАЛЬНЫХ НОД (END-NODES) ---
        print("===========================================================================")
        print(" END-NODES (Host Network Namespaces & OS)")
        print("===========================================================================\n")

        # 1. NGINX Target
        nginx_ns = SharedConfig.get('nodes.victim.net.netns', 'webserver')
        nginx_gw = SharedConfig.get('nodes.victim.net.gw', '10.0.40.2')
        res_nginx = self._run(f"ip netns exec {nginx_ns} ip route show")
        if res_nginx.returncode == 0 and f"default via {nginx_gw}" in res_nginx.stdout:
            nginx_status = f"{Colors.GREEN}[✓ OK]{Colors.ENDC}"
        else:
            nginx_status = f"{Colors.RED}[❌ MISSING/ERR]{Colors.ENDC}"

        print(f"[ 🎯 NGINX Target ({nginx_ns}) ]")
        print(f"ip netns exec {nginx_ns} ip route add default via {nginx_gw}  {nginx_status}\n")

        # 2. JMeter Generator
        jmeter_ns = SharedConfig.get('nodes.jmeter_node.net.netns', 'vlan500-jmeter')
        jmeter_gw = SharedConfig.get('nodes.jmeter_node.net.gw', '10.0.50.2')
        res_jm = self._run(f"ip netns exec {jmeter_ns} ip route show")
        if res_jm.returncode == 0 and f"default via {jmeter_gw}" in res_jm.stdout:
            jmeter_status = f"{Colors.GREEN}[✓ OK]{Colors.ENDC}"
        else:
            jmeter_status = f"{Colors.RED}[❌ MISSING/ERR]{Colors.ENDC}"

        print(f"[ 🔫 JMeter Generator ({jmeter_ns}) ]")
        print(f"ip netns exec {jmeter_ns} ip route add default via {jmeter_gw}  {jmeter_status}\n")

        # 3. TRex Generator
        trex_cfg = SharedConfig.get('nodes.trex_node.proc.trex.config', '/etc/trex_cfg_2.yaml')
        trex_gw_0 = SharedConfig.get('networks.external.gateway.addr', '10.0.50.2')
        trex_gw_1 = SharedConfig.get('networks.server_farm.gateway.addr', '10.0.70.2')
        
        print(f"[ 🦖 TRex Generator (Host OS + DPDK) ]")
        if os.path.exists(trex_cfg):
            print(f"Config File: {trex_cfg}  {Colors.GREEN}[✓ OK]{Colors.ENDC}")
            
            # Проверяем Client Port (0)
            chk0 = self._run(f"grep -q '{trex_gw_0}' {trex_cfg}")
            if chk0.returncode == 0:
                print(f"Client Port (0) GW : {trex_gw_0:<15}  {Colors.GREEN}[✓ OK]{Colors.ENDC}")
            else:
                print(f"Client Port (0) GW : {trex_gw_0:<15}  {Colors.RED}[❌ MISMATCH]{Colors.ENDC}")
                
            # Проверяем Server Port (1)
            chk1 = self._run(f"grep -q '{trex_gw_1}' {trex_cfg}")
            if chk1.returncode == 0:
                print(f"Server Port (1) GW : {trex_gw_1:<15}  {Colors.GREEN}[✓ OK]{Colors.ENDC}")
            else:
                print(f"Server Port (1) GW : {trex_gw_1:<15}  {Colors.RED}[❌ MISMATCH]{Colors.ENDC}")
        else:
            print(f"Config File: {trex_cfg}  {Colors.RED}[❌ NOT FOUND]{Colors.ENDC}")

        print("\n" + "-" * 75)

    def run_preflight_checks(self):
        """Проверяет связность L3 и L7 от генераторов до цели, включая проверку всех URI"""
        print(f"\n{Colors.BOLD}{Colors.CYAN}=== ✈️  Pre-flight Connectivity & Readiness Checks ==={Colors.ENDC}")
        
        # --- 0. ВАРНИНГ ПРО ОТКЛЮЧЕНИЕ ЗАЩИТЫ ---
        print(f"{Colors.YELLOW}{Colors.BOLD}⚠ ВНИМАНИЕ: Перед запуском проверок убедитесь, что защита на DUT ОТКЛЮЧЕНА{Colors.ENDC}")
        print(f"{Colors.YELLOW}{Colors.BOLD}  (режим bypass/пропускания) во избежание блокировок!{Colors.ENDC}\n")

        target_ip = SharedConfig.get('nodes.victim.net.ip')
        jmeter_ns = SharedConfig.get('nodes.jmeter_node.net.netns')
        nginx_ns = SharedConfig.get('nodes.victim.net.netns')

        if not jmeter_ns or not target_ip:
            print(f"{Colors.RED}[❌] Ошибка: Не найден nodes.jmeter_node.net.netns или nodes.victim.net.ip в конфиге!{Colors.ENDC}")
            return

        # --- 1. СВОДКА ПО ЛОКАЛЬНЫМ НАСТРОЙКАМ (SUMMARY) ---
        print(f"{Colors.BOLD}[ ⚙️  Local Nodes Summary ]{Colors.ENDC}")

        jm_iface = SharedConfig.get('nodes.jmeter_node.net.iface', 'unknown')
        jm_ip = SharedConfig.get('nodes.jmeter_node.net.ip', 'unknown')
        # Задействуем хелпер для проверки неймспейсов
        self._check_netns(jmeter_ns, jm_ip, jm_iface)

        print(f"{Colors.BOLD}  [📊 JMeter Pools Status]{Colors.ENDC}")
        self._print_pool_stats()

        nx_iface = SharedConfig.get('nodes.victim.net.iface', 'unknown')
        # Target Mode Check (Хитрый Grep по конфигам NGINX)
        mode_check = self._run("grep -q 'proxy_pass' /etc/nginx/sites-enabled/* 2>/dev/null")
        tgt_mode = "PROXY (Backend Mode)" if mode_check.returncode == 0 else "STATIC (Nginx Native)"
        self._check_netns(nginx_ns, target_ip, nx_iface)
        print(f"  [NS]  {'Target Mode':<15} : {Colors.BOLD}{tgt_mode}{Colors.ENDC}\n")




        print(f"{Colors.BOLD}[ 📡 Network Connectivity (JMeter -> NGINX {target_ip}) ]{Colors.ENDC}")

        # --- 2. ПИНГИ С РАЗНЫХ SOURCE IP (L3) ---
        print(f"  [1/3] ICMP Ping (L3)...")
        iface_ip = SharedConfig.get('nodes.jmeter_node.net.ip', '').split('/')[0]
        
        base_ip, attack_ip = None, None
        try:
            if os.path.exists(self.base_csv):
                with open(self.base_csv, 'r') as f: base_ip = next((line.strip() for line in f if line.strip()), None)
            if os.path.exists(self.attack_csv):
                with open(self.attack_csv, 'r') as f: attack_ip = next((line.strip() for line in f if line.strip()), None)
        except Exception:
            pass

        for label, src_ip in [("Interface IP", iface_ip), ("Baseline Pool", base_ip), ("Attack Pool", attack_ip)]:
            if not src_ip: continue
            print(f"      ↳ From {label:<15} ({src_ip:<14}): ", end="", flush=True)
            res_ping = self._run(f"ip netns exec {jmeter_ns} ping -c 1 -W 2 -I {src_ip} {target_ip} >/dev/null 2>&1")
            if res_ping.returncode == 0:
                print(f"[{Colors.GREEN}✓ OK{Colors.ENDC}]")
            else:
                print(f"[{Colors.RED}❌ FAIL{Colors.ENDC}]")

        # --- 3. ПРОВЕРКА ЭНДПОИНТОВ ИЗ КОНФИГА (L7) ---
        print(f"\n  [2/3] Explicit Endpoints Check (L7)...")
        
        services = SharedConfig.get('nodes.victim.services', {})
        if not services:
            print(f"      {Colors.YELLOW}[⚠ WARN] Секция 'services' пуста или не найдена в конфиге.{Colors.ENDC}")
        else:
            for srv_name, srv_data in services.items():
                proto = srv_data.get('protocol', 'http')
                port = srv_data.get('port', 80)
                path = srv_data.get('path', '/')
                
                # Формируем красивый URL (скрываем дефолтные порты для красоты)
                port_str = "" if (proto == "http" and port == 80) or (proto == "https" and port == 443) else f":{port}"
                url = f"{proto}://{target_ip}{port_str}{path}"
                
                print(f"      ↳ {url:<32}: ", end="", flush=True)
                
                # -k игнорирует ошибки самоподписанных SSL
                curl_opts = "-s -o /dev/null -w '%{http_code}' --connect-timeout 2 -k"
                res_curl = self._run(f"ip netns exec {jmeter_ns} curl {curl_opts} {url}")
                status = res_curl.stdout.strip()

                # Анализируем статусы
                if status in ["200", "201"]:
                    print(f"[{Colors.GREEN}✓ OK{Colors.ENDC}] (HTTP {status})")
                elif status in ["301", "302", "307"]:
                    print(f"[{Colors.YELLOW}⚠ REDIRECT{Colors.ENDC}] (HTTP {status} - DUT Check?)")
                elif status in ["401", "403", "404", "405"]:
                    # 401/404 означают, что NGINX жив и честно ответил! Для связности это ОК.
                    print(f"[{Colors.GREEN}✓ OK{Colors.ENDC}] (HTTP {status} - Valid Reply)")
                elif status in ["500", "502", "503", "504"]:
                    print(f"[{Colors.RED}❌ ERROR{Colors.ENDC}] (HTTP {status} - Backend Down)")
                else:
                    err_code = status if (status and status != "000") else "Timeout/Refused"
                    print(f"[{Colors.RED}❌ FAIL{Colors.ENDC}] ({err_code})")

        # --- 4. ПРОВЕРКА URIs ИЗ CSV ---
        print(f"\n  [3/3] URIs Validation (uris.csv)... ", end="", flush=True)
        uris_csv_path = os.path.join(self.profiles_dir, 'jmeter', 'data/uris.csv')
        
        if os.path.exists(uris_csv_path):
            tmp_script = "/tmp/pmi_uri_check.sh"
            # Пишем скрипт в файл, чтобы избежать любых проблем с экранированием кавычек в bash -c
            bash_script = f"""#!/bin/bash
success=0
total=0
while IFS= read -r uri || [ -n "$uri" ]; do
    uri=$(echo "$uri" | tr -d '\\r\\n')
    [ -z "$uri" ] && continue
    total=$((total+1))
    code=$(curl -s -o /dev/null -w "%{{http_code}}" --connect-timeout 1 "http://{target_ip}${{uri}}")
    # Считаем 200, 301 успешным ответом (эндпоинт живой)
    if [[ "$code" =~ ^(200|301)$ ]]; then 
        success=$((success+1))
    fi
done < {uris_csv_path}
echo "$success/$total"
"""
            with open(tmp_script, 'w') as f:
                f.write(bash_script)
                
            res_uris = self._run(f"ip netns exec {jmeter_ns} bash {tmp_script}")
            output = res_uris.stdout.strip()
            
            if os.path.exists(tmp_script):
                os.remove(tmp_script)
                
            try:
                success, total = map(int, output.split('/'))
                if total == 0:
                    print(f"[{Colors.YELLOW}⚠ EMPTY{Colors.ENDC}] (File is empty)")
                elif success == total:
                    print(f"[{Colors.GREEN}✓ SUCCESS{Colors.ENDC}] ({success}/{total} endpoints reachable [200/301])")
                elif success > 0:
                    print(f"[{Colors.YELLOW}⚠ PARTIAL{Colors.ENDC}] ({success}/{total} endpoints reachable)")
                    print(f"      {Colors.DIM}↳ Часть эндпоинтов отдают ошибки (404/50x). Проверьте бэкенд или пути в uris.csv!{Colors.ENDC}")
                else:
                    print(f"[{Colors.RED}❌ FAIL{Colors.ENDC}] (0/{total} endpoints reachable)")
            except Exception:
                err_details = res_uris.stderr.strip() or output
                print(f"[{Colors.RED}❌ ERROR{Colors.ENDC}] (Parsing failed)")
                print(f"      {Colors.DIM}↳ Details: {err_details}{Colors.ENDC}")
        else:
            print(f"[{Colors.YELLOW}⚠ SKIPPED{Colors.ENDC}] (File not found: {uris_csv_path})")

        print(f"\n{Colors.DIM}# Если проверки завершаются с ошибкой, проверьте:{Colors.ENDC}")
        print("#   1. Режим защиты DUT (OFF/Transparent).")
        print("#   2. Локальные настройки маршрутизации (Check Network Namespaces).")
        print("#   3. Настройки маршрутизации на стенде (Routing Cheatsheet).")
        print("-" * 75)

# === ФУНКЦИИ-ОБЕРТКИ ДЛЯ ВЫЗОВА ИЗ МЕНЮ ===

def _pause_if_needed():
    """
    Умная пауза. Срабатывает только при ручном запуске в консоли (TUI).
    Игнорируется, если передан флаг --batch, включен WEB_MODE или нет потока ввода.
    """
    is_batch = "--batch" in sys.argv
    is_web = os.environ.get("PMI_WEB_MODE") == "1"
    
    if not is_batch and not is_web:
        try:
            input(f"\n{Colors.BOLD}Press Enter to return...{Colors.ENDC}")
        except EOFError:
            pass # Если потока ввода нет (nohup, cron, web), просто идем дальше

def setup_jmeter_ns():
    NetworkSetup().setup_jmeter_ns()
    _pause_if_needed()

def provision_jmeter_ips():
    NetworkSetup().provision_jmeter_ips()
    _pause_if_needed()

def teardown_jmeter_ns():
    NetworkSetup().teardown_jmeter_ns()
    _pause_if_needed()

def show_routing_cheatsheet():
    NetworkSetup().show_routing_cheatsheet()
    _pause_if_needed()

def run_preflight_checks():
    NetworkSetup().run_preflight_checks()
    _pause_if_needed()

if __name__ == "__main__":
    # Если запустили напрямую из консоли (например: python3 network_setup.py setup --batch)
    if "setup" in sys.argv:
        setup_jmeter_ns()
    elif "provision" in sys.argv:
        provision_jmeter_ips()
    elif "teardown" in sys.argv:
        teardown_jmeter_ns()
    else:
        # По умолчанию просто создаем NetNS
        setup_jmeter_ns()