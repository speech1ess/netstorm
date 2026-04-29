#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import requests
import warnings
import ipaddress
import subprocess
import importlib.util
import threading
import queue

warnings.filterwarnings("ignore")

try:
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log

# ─────────────────────────────────────────────────────────────
# 1. TRex API Import
# ─────────────────────────────────────────────────────────────
trex_lib_path = SharedConfig.get('nodes.trex_node.api_path', '/opt/trex/automation/trex_control_plane/interactive')
if not os.path.exists(trex_lib_path):
    base_trex = '/opt/trex'
    if os.path.exists(base_trex):
        try:
            versions = [d for d in os.listdir(base_trex) if d.startswith('v')]
            if versions: trex_lib_path = os.path.join(base_trex, sorted(versions)[-1], 'automation/trex_control_plane/interactive')
        except: pass

if os.path.exists(trex_lib_path): sys.path.insert(0, trex_lib_path)

try:
    from trex.stl.api import STLClient, STLError, STLProfile
    from trex.astf.api import ASTFClient, ASTFProfile
except ImportError:
    Log.error(f"Could not import TRex API from {trex_lib_path}")
    sys.exit(1)


# ─────────────────────────────────────────────────────────────
# 2. УПРАВЛЕНИЕ ОС И СЕРВИСАМИ (OS MANAGER)
# ─────────────────────────────────────────────────────────────
class TRexServiceManager:
    def __init__(self):
        # 🟢 Исправлено под твой global.yaml (якоря &trex_base)
        proc_cfg = SharedConfig.get('nodes.trex_node.proc', {})
        self.svc_stl = proc_cfg.get('trex-stl', {}).get('service_name', 'trex-2')
        self.svc_astf = proc_cfg.get('trex-astf', {}).get('service_name', 'trex-2-astf')
        
        # Гарантируем суффикс .service
        if not self.svc_stl.endswith('.service'): self.svc_stl += '.service'
        if not self.svc_astf.endswith('.service'): self.svc_astf += '.service'

    def ensure_mode(self, target_mode: str):
        target_svc = self.svc_astf if target_mode == 'astf' else self.svc_stl
        other_svc = self.svc_stl if target_mode == 'astf' else self.svc_astf

        res = subprocess.run(['systemctl', 'is-active', target_svc], capture_output=True, text=True)
        if res.stdout.strip() == 'active': return

        Log.info(f"TRex Context Switch: Changing to {target_mode.upper()} mode ({target_svc})...")
        subprocess.run(['systemctl', 'stop', other_svc], check=False)
        time.sleep(1)
        
        if subprocess.run(['systemctl', 'start', target_svc], check=False).returncode != 0:
            Log.error(f"Failed to start {target_svc}! Is the systemd unit configured correctly?")
            sys.exit(1)
            
        Log.info("Waiting 15 seconds for DPDK and RPC server to initialize...")
        time.sleep(15)


# ─────────────────────────────────────────────────────────────
# 3. СБОР МЕТРИК (TELEMETRY - NON-BLOCKING)
# ─────────────────────────────────────────────────────────────
class TRexTelemetry:
    def __init__(self, log_name_base, profile_name):
        self.session_id = os.environ.get("PMI_RUN_ID", "manual")
        self.run_id = log_name_base
        self.profile = os.path.basename(profile_name)
        self.push_url = None
        
        mon = SharedConfig.get('nodes.monitor', {})
        if ip := mon.get('net', {}).get('ip'):
            port = mon.get('services', {}).get('victoria_api', {}).get('port', 8428)
            self.push_url = f"http://{ip}:{port}/api/v1/import/prometheus"

        # --- ФОНОВЫЙ ВОРКЕР ---
        self.queue = queue.Queue(maxsize=1000) # Буфер на 1000 метрик, чтобы не съесть память
        if self.push_url:
            self.worker = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker.start()

    def _worker_loop(self):
        """Читает очередь в фоне и шлет метрики по сети. Не блокирует генератор!"""
        while True:
            try:
                lines = self.queue.get()
                if lines is None: break # Сигнал к остановке (Poison Pill)
                requests.post(self.push_url, data="\n".join(lines), timeout=1)
            except Exception:
                pass # Игнорируем сетевые ошибки, главная задача - не упасть
            finally:
                self.queue.task_done()

    def push_stl(self, stats, ports):
        if not self.push_url: return
        timestamp = int(time.time() * 1000)
        lines = []
        for port in ports:
            if s := stats.get(port):
                lbl = f'run_id="{self.run_id}",session="{self.session_id}",profile="{self.profile}",port="{port}"'
                metrics = {
                    "pmi_trex_tx_pps": s.get('tx_pps', 0),
                    "pmi_trex_rx_pps": s.get('rx_pps', 0),
                    "pmi_trex_tx_bps": s.get('tx_bps', 0),
                    "pmi_trex_rx_bps": s.get('rx_bps', 0),
                }
                lines.extend([f'{k}{{{lbl}}} {v} {timestamp}' for k, v in metrics.items()])
        self._send(lines)

    def push_astf(self, stats, ports):
        if not self.push_url: return
        timestamp = int(time.time() * 1000)
        lines = []
        lbl = f'run_id="{self.run_id}",session="{self.session_id}",profile="{self.profile}"'
        
        if traffic := stats.get('traffic'):
            client = traffic.get('client', {})
            metrics = {
                "pmi_trex_astf_active_flows": client.get('tcps_connattempt', 0) - client.get('tcps_closed', 0),
                "pmi_trex_astf_cps": client.get('tcps_connattempt', 0),
                "pmi_trex_astf_tx_bps": client.get('tx_bps', 0),
                "pmi_trex_astf_rx_bps": client.get('rx_bps', 0),
                "pmi_trex_astf_err_drop": client.get('tcps_drops', 0)
            }
            lines.extend([f'{k}{{{lbl}}} {max(0, v)} {timestamp}' for k, v in metrics.items()])
        self._send(lines)

    def _send(self, lines):
        """Неблокирующая отправка в очередь"""
        if lines and self.push_url:
            try:
                self.queue.put_nowait(lines)
            except queue.Full:
                pass # БД легла или сеть тормозит - просто дропаем метрики, но спасаем тест

    def stop(self):
        """Аккуратное завершение потока при остановке теста"""
        if self.push_url:
            try: self.queue.put_nowait(None)
            except: pass

# ─────────────────────────────────────────────────────────────
# 4. ДРАЙВЕР УПРАВЛЕНИЯ ТРАФИКОМ
# ─────────────────────────────────────────────────────────────
class TRexDriver:
    def __init__(self, profile_path, mult_str, duration, log_name_base, tunables):
        self.profile_path = profile_path
        self.mult_str = mult_str
        self.duration = duration
        self.tunables = tunables
        
        self.trex_ip    = SharedConfig.get('nodes.trex_node.net.ip', '127.0.0.1')
        self.ports      = SharedConfig.get('nodes.trex_node.net.trex_ports', [0, 1])
        self.sync_port  = SharedConfig.get('nodes.trex_node.services.zmq_sync.port', 4503)
        self.async_port = SharedConfig.get('nodes.trex_node.services.zmq_async.port', 4502)
        
        self.telemetry = TRexTelemetry(log_name_base, profile_path)
        self.svc_manager = TRexServiceManager()

    def detect_mode(self) -> str:   
        """Гибридный детектор: Имя файла + Строгие импорты API"""
        fname = os.path.basename(self.profile_path).lower()
        
        # 1. Проверяем префикс
        name_mode = 'unknown'
        if 'astf_' in fname: name_mode = 'astf'
        elif 'stl_' in fname: name_mode = 'stl'

        # 2. Проверяем содержимое (строгие импорты TRex API)
        content_mode = 'unknown'
        try:
            with open(self.profile_path, 'r', encoding='utf-8') as f:
                content = f.read(4096)
                if 'trex.astf.api' in content:
                    content_mode = 'astf'
                elif 'trex.stl.api' in content or 'trex_stl_lib.api' in content:
                    content_mode = 'stl'
        except Exception as e:
            Log.warning(f"Failed to read profile for detection: {e}")

        # 3. Принимаем решение (Импорты бьют имя файла)
        final_mode = content_mode if content_mode != 'unknown' else (name_mode if name_mode != 'unknown' else 'stl')

        # 4. Воспитываем
        if content_mode != 'unknown':
            if name_mode == 'unknown':
                Log.warning(f"TRex: Поняли, что это {content_mode.upper()}, но имя '{fname}' ни о чем не говорит. Добавь префикс 'astf_' или 'stl_'.")
            elif name_mode != content_mode:
                Log.warning(f"TRex: Имя файла говорит '{name_mode.upper()}', а импорты '{content_mode.upper()}'. Верим импортам. Переименуй файл, не путай людей!")
        elif name_mode == 'unknown':
            Log.warning(f"TRex: Детектор не смог определить режим для '{fname}'. Запускаем как STL на свой страх и риск.")

        Log.info(f"TRex Mode Detection Result: {final_mode.upper()}")
        return final_mode

    def run(self):
        mode = self.detect_mode()
        Log.info(f"TRex Effective Config: profile={os.path.basename(self.profile_path)}, mult={self.mult_str}, dur={self.duration}s")
        Log.info(f"TRex Active Tunables: {json.dumps(self.tunables)}")
        self.svc_manager.ensure_mode(mode)
        
        if mode == 'astf':
            self._run_astf()
        else:
            self._run_stl()

    def _build_l3_config(self):
        """Динамически собирает L3 настройки из global.yaml"""
        l3_cfg = {}
        interfaces = SharedConfig.get('nodes.trex_node.net.interfaces', {})
        networks = SharedConfig.get('networks', {})

        for port_key, data in interfaces.items():
            if port_key.startswith('port_'):
                p_num = int(port_key.split('_')[1])
                src_ip = data.get('addr')
                gw_ip = networks.get(data.get('network_ref', ''), {}).get('gateway', {}).get('addr')
                if src_ip and gw_ip: l3_cfg[p_num] = {'ip': src_ip, 'gw': gw_ip}
                
        if not l3_cfg:
            Log.warning("Could not build L3 topology from global.yaml. Using defaults.")
            l3_cfg = {0: {'ip': '10.0.50.3', 'gw': '10.0.50.2'}, 1: {'ip': '10.0.70.2', 'gw': '10.0.70.1'}}
        return l3_cfg

    def _run_stl(self):
        l3_config = self._build_l3_config()
        c = STLClient(verbose_level='error', server=self.trex_ip, sync_port=self.sync_port, async_port=self.async_port)
        
        import threading
        stop_event = threading.Event()

        def cleanup():
            # 🟢 Обработчик сигнала только взводит флаг
            Log.warning("🔴 [TRex Driver] Caught termination signal. Setting stop flag for STL...")
            stop_event.set()
            
        SharedTrap.register(cleanup)

        try:
            c.connect()
            c.acquire(ports=self.ports, force=True)
            c.reset(ports=self.ports)
            
            c.set_service_mode(ports=self.ports, enabled=True)
            time.sleep(1)

            active_ports = []
            for p in self.ports:
                if cfg := l3_config.get(p):
                    Log.info(f"Port {p}: Configuring L3 (src: {cfg['ip']}, dst: {cfg['gw']})")
                    c.set_l3_mode(port=p, src_ipv4=cfg['ip'], dst_ipv4=cfg['gw'])
                    active_ports.append(p)

            if active_ports:
                Log.info("Triggering ARP resolve...")
                c.resolve(ports=active_ports)
                time.sleep(2)
                for p in active_ports: Log.success(f"Port {p}: L3 state updated.")

            c.set_service_mode(ports=self.ports, enabled=False)
            
            Log.info(f"Loading STL Profile: {os.path.basename(self.profile_path)}")
            profile = STLProfile.load_py(self.profile_path, tunables=self.tunables)
            
            c.add_streams(profile.get_streams(), ports=[self.ports[0]])
            c.start(ports=[self.ports[0]], mult=self.mult_str, duration=self.duration)
            
            start_ts = time.time()
            last_log_ts = 0

            # 🟢 В условии цикла проверяем состояние флага остановки
            while c.is_traffic_active(ports=self.ports) and not stop_event.is_set():
                time.sleep(1)
                elapsed = int(time.time() - start_ts)
                now = time.time()

                try:
                    stats = c.get_stats(ports=self.ports)
                    
                    if hasattr(self, 'telemetry') and self.telemetry.push_url:
                        if not getattr(self.telemetry, 'worker', None) or not self.telemetry.worker.is_alive():
                            pass
                        self.telemetry.push_stl(stats, self.ports)

                    if now - last_log_ts >= 3:
                        tx_p, rx_p = self.ports[0], self.ports[1] if len(self.ports) > 1 else self.ports[0]
                        tx_pps, tx_bps = stats[tx_p].get('tx_pps', 0), stats[tx_p].get('tx_bps', 0)
                        rx_pps = stats[rx_p].get('rx_pps', 0)
                        
                        tx_str = f"{tx_pps/1e6:.2f}M" if tx_pps > 1e6 else f"{tx_pps/1e3:.1f}k"
                        bps_str = f"{tx_bps/1e9:.1f}G" if tx_bps > 1e9 else f"{tx_bps/1e6:.1f}M"
                        
                        import sys
                        sys.stdout.flush()
                        
                        Log.info(f"[{elapsed:3d}s] TX: {tx_str} pps ({bps_str}bps) | RX: {rx_pps:.0f} pps")
                        last_log_ts = now
                except STLError: 
                    pass
                except Exception as e:
                    Log.error(f"[{elapsed:3d}s] STL Stats Error: {e}")
                    import sys
                    sys.stdout.flush()

                if elapsed > self.duration + 5: 
                    Log.warning("Duration exceeded limit. Breaking loop.")
                    break

            # 🟢 Безопасная процедура завершения в основном потоке
            if stop_event.is_set():
                Log.warning("STL Traffic loop aborted by Kill Switch. Executing safe shutdown sequence...")

            if c.is_connected():
                c.stop(ports=self.ports)
                try: 
                    self.telemetry.push_stl(c.get_stats(ports=self.ports), self.ports)
                except: 
                    pass
                c.release(ports=self.ports)
                
            if hasattr(self, 'telemetry'):
                self.telemetry.stop()
                
            c.disconnect()
            Log.success("TRex STL test finished gracefully.")

        except Exception as e:
            Log.error(f"Execution Error: {e}")
            import sys
            sys.exit(1)

    def _run_astf(self):
        c = ASTFClient(server=self.trex_ip, sync_port=self.sync_port, async_port=self.async_port)
        
        import threading
        stop_event = threading.Event()

        def cleanup():
            # 🟢 Обработчик сигнала ТОЛЬКО ставит флаг. Никакого сетевого I/O!
            Log.warning("🔴 [TRex Driver] Caught termination signal. Setting stop flag...")
            stop_event.set()
            
        SharedTrap.register(cleanup)

        try:
            c.connect()
            c.reset()
            c.clear_stats()
            
            spec = importlib.util.spec_from_file_location("astf_profile", self.profile_path)
            astf_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(astf_mod)
            
            Log.info(f"Registering ASTF Profile: {os.path.basename(self.profile_path)}")
            profile = astf_mod.register(tunables=self.tunables)
            
            c.load_profile(profile)
            Log.info(f"Starting ASTF traffic... CPS: {self.mult_str} x1000, Duration: {self.duration}s")
            c.start(mult=float(self.mult_str), duration=self.duration)
            
            start_ts = time.time()
            last_log_ts = 0
            
            last_tcp_attempt = 0 
            last_udp_flows = 0

            # 🟢 В условии цикла проверяем состояние флага остановки
            while c.is_traffic_active() and not stop_event.is_set():
                time.sleep(1)
                now = time.time()
                elapsed = int(now - start_ts)

                try:
                    stats = c.get_stats()
                    
                    if hasattr(self, 'telemetry') and self.telemetry.push_url:
                        if not getattr(self.telemetry, 'worker', None) or not self.telemetry.worker.is_alive():
                            pass # Worker is optional or failed, keep going
                        self.telemetry.push_astf(stats, getattr(self, 'ports', [])) 

                    if now - last_log_ts >= 3:
                        time_delta = now - last_log_ts
                        total_stats = stats.get('global', stats.get('total', {}))
                        client = stats.get('traffic', {}).get('client', {})
                        
                        tx_bps = total_stats.get('tx_bps', 0)
                        rx_bps = total_stats.get('rx_bps', 0)
                        
                        if tx_bps == 0: tx_bps = client.get('m_tx_bps', 0)
                        if rx_bps == 0: rx_bps = client.get('m_rx_bps', 0)
                            
                        tcp_attempt = client.get('tcps_connattempt', 0)
                        tcp_closed = client.get('tcps_closed', 0)
                        tcp_active = max(0, tcp_attempt - tcp_closed)
                        tcp_drops = client.get('tcps_drops', 0)
                        tcp_cps = (tcp_attempt - last_tcp_attempt) / time_delta
                        
                        udp_flows = client.get('udps_accepts', client.get('udps_sndpkt', 0))
                        udp_drops = client.get('udps_noportbcast', 0)
                        udp_cps = (udp_flows - last_udp_flows) / time_delta
                        
                        last_tcp_attempt = tcp_attempt
                        last_udp_flows = udp_flows
                        
                        tx_str = f"{tx_bps/1e9:.1f}G" if tx_bps > 1e9 else f"{tx_bps/1e6:.1f}M"
                        rx_str = f"{rx_bps/1e9:.1f}G" if rx_bps > 1e9 else f"{rx_bps/1e6:.1f}M"
                        
                        # Принудительный сброс буфера логов
                        import sys
                        
                        if elapsed < 3 or (tcp_cps <= 5 and udp_cps <= 5 and tcp_active == 0):
                            Log.info(f"[{elapsed:3d}s] ASTF INIT | Protocol Detection Phase... | TX: {tx_str}bps | RX: {rx_str}bps")
                        else:
                            is_tcp = tcp_cps > 5 or tcp_active > 0
                            is_udp = udp_cps > 5
                            
                            if is_tcp and is_udp:
                                total_drops = tcp_drops + udp_drops
                                drop_str = f" | Drops: {total_drops}"
                                Log.info(f"[{elapsed:3d}s] ASTF MIX | TCP Flows: {tcp_active} | UDP CPS: {udp_cps:.0f} | TX: {tx_str}bps | RX: {rx_str}bps{drop_str}")
                            elif is_udp:
                                Log.info(f"[{elapsed:3d}s] ASTF UDP | CPS: {udp_cps:.0f} | TX: {tx_str}bps | RX: {rx_str}bps | Drops: {udp_drops}")
                            else:
                                Log.info(f"[{elapsed:3d}s] ASTF TCP | Active Flows: {tcp_active} | TX: {tx_str}bps | RX: {rx_str}bps | Drops: {tcp_drops}")                        
                        last_log_ts = now
                        
                        sys.stdout.flush()

                except Exception as e:
                    Log.error(f"[{elapsed:3d}s] ASTF Stats Error: {e}")
                    import sys
                    sys.stdout.flush()

                if elapsed > self.duration + 5: 
                    Log.warning("Duration exceeded limit. Breaking loop.")
                    break

            # 🟢 Главный цикл завершен (или по таймеру, или по сигналу остановки)
            if stop_event.is_set():
                 Log.warning("Traffic loop aborted by Kill Switch. Executing safe shutdown sequence...")
            
            # Теперь мы безопасно вызываем RPC в контексте главного потока
            if c.is_connected():
                c.stop()
                try: 
                    self.telemetry.push_astf(c.get_stats(), getattr(self, 'ports', []))
                except: 
                    pass
            
            if hasattr(self, 'telemetry'):
                 self.telemetry.stop()
                 
            c.disconnect()
            Log.success("TRex ASTF test finished gracefully.")

        except Exception as e:
            Log.error(f"Execution Error: {e}")
            import sys
            sys.exit(1)
# ─────────────────────────────────────────────────────────────
# 5. УТИЛИТЫ ДАННЫХ
# ─────────────────────────────────────────────────────────────
def parse_tunables(raw_json_str):
    try:
        params = json.loads(raw_json_str)
        for k, v in list(params.items()):
            if isinstance(v, str) and k.endswith('_pool'):
                try:
                    net = ipaddress.IPv4Network(v, strict=False)
                    prefix = k.replace('_pool', '')
                    
                    # Если это одиночный IP (или /32), у него всего 1 адрес [0]
                    if net.num_addresses == 1:
                        params[f'{prefix}_start'] = str(net[0])
                        params[f'{prefix}_end'] = str(net[0])
                    else:
                        # Если это нормальная подсеть, берем первый [1] и последний [-2]
                        params[f'{prefix}_start'] = str(net[1])
                        params[f'{prefix}_end'] = str(net[-2])
                        
                    del params[k]
                    Log.info(f"TRex Adapter: Translated {k} to {prefix}_start/end")
                except Exception as e: 
                    Log.warning(f"Failed to parse subnet {v} for key {k}: {e}")
        return params
    except json.JSONDecodeError as e:
        Log.error(f"Failed to parse tunables JSON: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 5:
        Log.error(f"Usage: {sys.argv[0]} <profile> <mult> <dur> <LOG_NAME_BASE> [tunables_json]")
        sys.exit(1)

    profile_path = sys.argv[1]
    mult_str     = sys.argv[2]
    duration     = int(sys.argv[3])
    log_name_base = sys.argv[4]
    
    # 🟢 Парсим tunables и передаём в драйвер
    tunables = parse_tunables(sys.argv[5]) if len(sys.argv) >= 6 else {}
    Log.info(f"TREX DRIVER START: {os.path.basename(profile_path)}")

    driver = TRexDriver(profile_path, mult_str, duration, log_name_base, tunables)
    driver.run()