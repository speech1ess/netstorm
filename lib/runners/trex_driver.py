#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sys
import time
import json
import glob
import requests
import warnings
import ipaddress
import subprocess
import importlib.util
import threading
import queue
import copy  # <-- ДОБАВЛЕНО ДЛЯ СНИМКОВ ПАМЯТИ
from pathlib import Path
from typing import Dict, List, Tuple, Any

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
            # 🟢 ДОБАВЛЕН ЛОГ: Сразу видим, правильный ли URL
            Log.info(f"📡 Telemetry Push URL: {self.push_url}") 
        else:
            Log.warning("⚠️ Telemetry DISABLED: Monitor IP not found in global.yaml")

        # --- ФОНОВЫЙ ВОРКЕР ---
        self.queue = queue.Queue(maxsize=1000) 
        if self.push_url:
            self.worker = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker.start()

    def _worker_loop(self):
        """Читает очередь в фоне и шлет метрики. Пишет ошибки, если база легла."""
        while True:
            try:
                lines = self.queue.get()
                if lines is None: break 
                
                # 🟢 ФИКС: Обязательный перенос строки в конце payload
                payload = "\n".join(lines) + "\n"
                
                # 🟢 ФИКС: Таймаут увеличен до 2с, чтобы база успела ответить
                resp = requests.post(self.push_url, data=payload, timeout=2)
                
                if resp.status_code >= 400:
                    Log.error(f"🔴 VictoriaMetrics Error {resp.status_code}: {resp.text}")
                    
            except requests.exceptions.Timeout:
                pass # Сетевые задержки игнорим, чтобы не спамить в консоль
            except Exception as e:
                Log.error(f"🔴 Telemetry Worker Exception: {e}")
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
                    "netstorm_stl_tx_pps": s.get('tx_pps', 0),
                    "netstorm_stl_rx_pps": s.get('rx_pps', 0),
                    "netstorm_stl_tx_bps": s.get('tx_bps', 0),
                    "netstorm_stl_rx_bps": s.get('rx_bps', 0),
                    "netstorm_stl_l2_drops": max(0, s.get('opackets', 0) - s.get('ipackets', 0))
                }
                for k, v in metrics.items():
                    try: lines.append(f'{k}{{{lbl}}} {max(0.0, float(v))} {timestamp}')
                    except (ValueError, TypeError): pass
        self._send(lines)

    def push_astf(self, stats, ports):
        if not self.push_url: return
        timestamp = int(time.time() * 1000)
        lines = []
        lbl = f'run_id="{self.run_id}",session="{self.session_id}",profile="{self.profile}"'
        
        metrics = {}

        # Вытаскиваем словари (с фоллбэками на разные версии TRex)
        total_stats = stats.get('global', stats.get('total', {}))
        client = stats.get('traffic', {}).get('client', {})

        # Умный парсинг скорости (Берем из Total, если нет - из Client)
        l2_tx_bps = total_stats.get('tx_bps', 0)
        l2_rx_bps = total_stats.get('rx_bps', 0)
        if l2_tx_bps == 0: l2_tx_bps = client.get('m_tx_bps', 0)
        if l2_rx_bps == 0: l2_rx_bps = client.get('m_rx_bps', 0)

        # 1. L7 (Application) & General Traffic Stats
        metrics.update({
            "netstorm_astf_active_flows": client.get('tcps_connattempt', 0) - client.get('tcps_closed', 0),
            "netstorm_astf_cps": client.get('tcps_connattempt', 0),
            "netstorm_astf_tx_bps": l2_tx_bps,
            "netstorm_astf_rx_bps": l2_rx_bps,
            "netstorm_astf_l7_drops": client.get('tcps_drops', 0)
        })

        # 2. L2 (Physical) Stats
        metrics.update({
            "netstorm_l2_tx_bps": l2_tx_bps,
            "netstorm_l2_rx_bps": l2_rx_bps,
            "netstorm_l2_drop_bps": total_stats.get('rx_drop_bps', 0)
        })

        # 3. Latency (Парсинг гистограмм по портам, конвертация usec -> ms)
        if latency := stats.get('latency'):
            max_delays = []
            avg_delays = []
            for port_id, port_data in latency.items():
                if isinstance(port_data, dict) and 'hist' in port_data:
                    hist = port_data['hist']
                    max_delays.append(hist.get('max_usec', 0))
                    avg_delays.append(hist.get('s_avg', 0))
            if max_delays and avg_delays:
                metrics.update({
                    "netstorm_latency_max_ms": max(max_delays) / 1000.0,
                    "netstorm_latency_avg_ms": (sum(avg_delays) / len(avg_delays)) / 1000.0
                })

        # 4. Malware / IPS (Поиск в Traffic Groups) 
        client_traffic = stats.get('traffic', {}).get('client', {})
        tg_names = client_traffic.get('tg_names', {})
        malware = tg_names.get('malware', {})
        
        if malware:
            mc = malware.get('client', {})
            ms = malware.get('server', {})
            
            # 1. Отправлено малвари (TCP attempts + UDP sent packets)
            m_tx_tcp = mc.get('tcps_connattempt', 0)
            m_tx_udp = mc.get('udps_sndpkt', 0)
            malware_sent = m_tx_tcp + m_tx_udp
            
            # 2. Заблокировано TCP (drops + testdrops)
            m_drops_tcp = mc.get('tcps_drops', 0) + mc.get('tcps_testdrops', 0)
            
            # 3. Заблокировано UDP (Двусторонняя проверка потери пакетов)
            m_udp_c2s_drops = max(0, m_tx_udp - ms.get('udps_rcvpkt', 0))
            m_udp_s2c_drops = max(0, ms.get('udps_sndpkt', 0) - mc.get('udps_rcvpkt', 0))
            
            ips_blocks = m_drops_tcp + m_udp_c2s_drops + m_udp_s2c_drops
            
            metrics.update({
                "netstorm_ips_sent": malware_sent,
                "netstorm_ips_blocked": ips_blocks
            })
            
        # 5. TCP Health (Ретрансмиты - индикатор переполнения буферов SUT)
        metrics.update({
            "netstorm_tcp_rexmit_pkts": client.get('tcps_sndrexmitpack', 0),
            "netstorm_tcp_rexmit_bytes": client.get('tcps_sndrexmitbyte', 0)
        })

        # Безопасный парсинг значений
        for k, v in metrics.items():
            if v is not None:
                try:
                    val = max(0.0, float(v))
                    lines.append(f'{k}{{{lbl}}} {val} {timestamp}')
                except (ValueError, TypeError):
                    pass

        self._send(lines)

    def _send(self, lines):
        if lines and self.push_url:
            try: self.queue.put_nowait(lines)
            except queue.Full: pass

    def stop(self):
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
        
        stop_event = threading.Event()

        def cleanup():
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

            session_peak_bps = 0.0

            while c.is_traffic_active():
                time.sleep(1)
                now = time.time()
                elapsed = int(now - start_ts)

                try:
                    stats = c.get_stats()
                    
                    # 1. Проверяем, жив ли фоновый поток телеметрии
                    if hasattr(self, 'telemetry') and self.telemetry.push_url:
                        if not self.telemetry.worker.is_alive():
                            Log.warning(f"[{elapsed:3d}s] TELEMETRY WORKER IS DEAD!")
                        self.telemetry.push_astf(stats, getattr(self, 'ports', [])) 

                    if now - last_log_ts >= 3:
                        total_stats = stats.get('total', {})
                        
                        tx_bps = total_stats.get('tx_bps', 0)
                        rx_bps = total_stats.get('rx_bps', 0)
                        tx_pps = total_stats.get('tx_pps', 0)
                        rx_pps = total_stats.get('rx_pps', 0)

                        # 🟢 ИСПОЛЬЗУЕМ АБСОЛЮТНЫЕ СЧЕТЧИКИ ДЛЯ ТОЧНОСТИ (Одометр, а не спидометр)
                        opackets = total_stats.get('opackets', 0)
                        ipackets = total_stats.get('ipackets', 0)

                        # 🟢 ВЫЧИСЛЯЕМ L2 ДРОПЫ ПО СУММЕ ПАКЕТОВ (защита от микро-задержек)
                        drops_total = max(0, opackets - ipackets)
                        drop_pct = (drops_total / opackets * 100.0) if opackets > 0 else 0.0

                        # ОБНОВЛЯЕМ ПИК
                        if tx_bps > session_peak_bps:
                            session_peak_bps = tx_bps
                            
                        # Форматируем биты
                        tx_str = f"{tx_bps/1e9:.2f}G" if tx_bps > 1e9 else f"{tx_bps/1e6:.1f}M"
                        rx_str = f"{rx_bps/1e9:.2f}G" if rx_bps > 1e9 else f"{rx_bps/1e6:.1f}M"
                        
                        # Форматируем пакеты
                        tx_p_str = f"{tx_pps/1e6:.2f}M" if tx_pps >= 1e6 else f"{tx_pps/1e3:.1f}K"
                        rx_p_str = f"{rx_pps/1e6:.2f}M" if rx_pps >= 1e6 else f"{rx_pps/1e3:.1f}K"
                        
                        sys.stdout.flush()
                        
                        # 🟢 ВЫВОДИМ ДРОПЫ В КОНСОЛЬ
                        Log.info(f"[{elapsed:3d}s] STL TRAFFIC | TX: {tx_str}bps ({tx_p_str}pps) | RX: {rx_str}bps ({rx_p_str}pps) | Drops: {drop_pct:.4f}%")                        
                        last_log_ts = now
                        
                except Exception as e:
                    # 3. Печатаем ВСЕ ошибки без ограничений по времени!
                    Log.error(f"[{elapsed:3d}s] CRITICAL ASTF Stats Error: {e}")
                    import traceback
                    traceback.print_exc() # Выплевываем полный трейсбэк
                    sys.stdout.flush()

                if elapsed > self.duration + 5: 
                    Log.warning("Duration exceeded limit. Breaking loop.")
                    break

            if stop_event.is_set():
                Log.warning("STL Traffic loop aborted by Kill Switch. Executing safe shutdown sequence...")

            if c.is_connected():
                c.stop(ports=self.ports)
                try: 
                    final_stats = c.get_stats(ports=self.ports)
                    
                    # 🟢 Агрегация L2 пакетов
                    total = final_stats.get('total', {})
                    final_stats['tx_pkts'] = total.get('opackets', 0) 
                    final_stats['rx_pkts'] = total.get('ipackets', 0)
                    
                    if hasattr(self, 'telemetry'):
                        self.telemetry.push_stl(final_stats, self.ports)
                    
                    final_stats['custom_peak_bps'] = session_peak_bps

                    # 🟢 ЖЕСТКАЯ АДРЕСАЦИЯ
                    log_name_base = getattr(self.telemetry, 'run_id', 'unknown_run')
                    stats_filename = f"stats_{log_name_base}.json"
                    
                    session_id = os.environ.get("PMI_RUN_ID")
                    if session_id:
                        log_dir = os.path.join(SharedConfig.get('paths.logs', '/opt/pmi/logs'), session_id)
                    else:
                        log_dir = os.getcwd()
                    
                    os.makedirs(log_dir, exist_ok=True)
                    save_path = os.path.join(log_dir, stats_filename)
                    
                    Log.info(f"💾 Attempting to save JSON artifact to: {save_path}")
                    
                    # 🟢 FORCE DISK FLUSH (Фикс Race Condition)
                    with open(save_path, 'w', encoding='utf-8') as f:
                        json.dump(final_stats, f, indent=2)
                        f.flush()            # Сбрасываем буфер Питона в ОС
                        os.fsync(f.fileno()) # Приказываем ядру Linux сбросить кэш на диск

                    Log.success(f"📊 [Telemetry] Final STL stats successfully dumped to {save_path}")

                except Exception as e: 
                    # 🟢 ТОТ САМЫЙ EXCEPT, КОТОРЫЙ Я СРЕЗАЛ В ПРОШЛЫЙ РАЗ!
                    Log.error(f"⚠️ [FATAL I/O ERROR] Failed to process/write JSON: {e}")
                    
                # Освобождаем порты ВНЕ блока try-except
                c.release(ports=self.ports)
                
            if hasattr(self, 'telemetry'):
                self.telemetry.stop()
            
            c.disconnect()
            Log.success("TRex STL test finished gracefully.")

        except Exception as e:
            Log.error(f"Execution Error: {e}")
            sys.exit(1)

    def _run_astf(self):
        c = ASTFClient(server=self.trex_ip, sync_port=self.sync_port, async_port=self.async_port)
        
        stop_event = threading.Event()

        def cleanup():
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
            # Добавляем поддержку latency из tunables или по дефолту
            latency_pps = self.tunables.get('latency_pps', 1000)
            c.start(mult=float(self.mult_str), duration=self.duration, latency_pps=latency_pps)
            
            start_ts = time.time()
            last_log_ts = 0
            last_tcp_attempt = 0 
            last_udp_flows = 0
            session_peak_bps = 0.0
            
            # 🟢 ПЕРЕМЕННАЯ ДЛЯ ХРАНЕНИЯ ЧИСТОГО СНИМКА МЕТРИК
            clean_stats = None

            while c.is_traffic_active() and not stop_event.is_set():
                time.sleep(1)
                now = time.time()
                elapsed = int(now - start_ts)
                time_left = self.duration - elapsed

                try:
                    stats = c.get_stats()
                    
                    # 🟢 МАГИЯ: Делаем слепок за 3 секунды до конца (до того, как TRex порубит TCP)
                    if time_left <= 3 and clean_stats is None:
                        clean_stats = copy.deepcopy(stats)
                        # Обогащаем слепок тегами TG Stats прямо здесь, пока профиль жив
                        try:
                            if hasattr(c, 'get_tg_names'):
                                tg_names = c.get_tg_names()
                                if tg_names:
                                    tg_stats = c.get_traffic_tg_stats(tg_names)
                                    if 'traffic' in clean_stats and 'client' in clean_stats['traffic']:
                                        clean_stats['traffic']['client']['tg_names'] = tg_stats
                        except Exception as e:
                            Log.error(f"⚠️ [Driver] Failed to fetch ASTF TG stats for snapshot: {e}")
                        Log.info("📸 Сделан чистый снимок метрик до начала Teardown-хвоста (Игнорируем RST-дропы)")
                    
                    if hasattr(self, 'telemetry') and self.telemetry.push_url:
                        if getattr(self.telemetry, 'worker', None) and self.telemetry.worker.is_alive():
                            self.telemetry.push_astf(stats, getattr(self, 'ports', [])) 

                    if now - last_log_ts >= 3:
                        time_delta = now - last_log_ts
                        total_stats = stats.get('global', stats.get('total', {}))
                        client = stats.get('traffic', {}).get('client', {})
                        
                        tx_bps = total_stats.get('tx_bps', 0)
                        rx_bps = total_stats.get('rx_bps', 0)
                        
                        if tx_bps == 0: tx_bps = client.get('m_tx_bps', 0)
                        if rx_bps == 0: rx_bps = client.get('m_rx_bps', 0)
                        
                        # 🟢 ОБНОВЛЯЕМ ПИК
                        if tx_bps > session_peak_bps:
                            session_peak_bps = tx_bps
                            
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
                        
                        if elapsed < 3 or (tcp_cps <= 5 and udp_cps <= 5 and tcp_active == 0):
                            Log.info(f"[{elapsed:3d}s] ASTF INIT | Protocol Detection Phase... | TX: {tx_str}bps | RX: {rx_str}bps")
                        else:
                            is_tcp = tcp_cps > 5 or tcp_active > 0
                            is_udp = udp_cps > 5
                            
                            if is_tcp and is_udp:
                                total_drops = tcp_drops + udp_drops
                                drop_str = f" | Total Drops: {total_drops}"
                                Log.info(f"[{elapsed:3d}s] ASTF MIX | TCP Flows: {tcp_active} | UDP CPS: {udp_cps:.0f} | TX: {tx_str}bps | RX: {rx_str}bps{drop_str}")
                            elif is_udp:
                                Log.info(f"[{elapsed:3d}s] ASTF UDP | CPS: {udp_cps:.0f} | TX: {tx_str}bps | RX: {rx_str}bps | Total Drops: {udp_drops}")
                            else:
                                Log.info(f"[{elapsed:3d}s] ASTF TCP | Active Flows: {tcp_active} | TX: {tx_str}bps | RX: {rx_str}bps | Total Drops: {tcp_drops}")                        
                        
                        sys.stdout.flush()
                        last_log_ts = now

                except Exception as e:
                    Log.error(f"[{elapsed:3d}s] ASTF Stats Error: {e}")
                    sys.stdout.flush()

                if elapsed > self.duration + 5: 
                    Log.warning("Duration exceeded limit. Breaking loop.")
                    break

            # 🟢 ДИАГНОСТИКА: Почему мы вышли из цикла?
            if stop_event.is_set():
                 Log.warning("Traffic loop aborted by Kill Switch (SIGTERM/SIGINT received).")
            elif elapsed > self.duration + 5:
                 Log.warning(f"Traffic loop ended by Timeout. Elapsed: {elapsed}s, Limit: {self.duration + 5}s.")
            elif not c.is_traffic_active():
                 Log.success(f"Traffic loop ended. TRex finished transmission after {elapsed}s.")
            else:
                 Log.error(f"Traffic loop ended abnormally! Unknown reason. Elapsed: {elapsed}s.")
            
            if c.is_connected():
                c.stop()
                try: 
                    # 🟢 ИСПОЛЬЗУЕМ СНИМОК ЕСЛИ ЕСТЬ, ИНАЧЕ БЕРЕМ ГРЯЗНЫЕ ДАННЫЕ
                    final_stats = clean_stats if clean_stats else c.get_stats()
                    
                    # =========================================================
                    # 🟢 DATA-DRIVEN: ЭКСТРАКЦИЯ ТЕГОВ (Если снимок не сработал)
                    # =========================================================
                    if not clean_stats:
                        try:
                            if hasattr(c, 'get_tg_names'):
                                tg_names = c.get_tg_names()
                                if tg_names:
                                    tg_stats = c.get_traffic_tg_stats(tg_names)
                                    if 'traffic' in final_stats and 'client' in final_stats['traffic']:
                                        final_stats['traffic']['client']['tg_names'] = tg_stats
                                        Log.success("🎯 ASTF TG Stats successfully injected into telemetry payload.")
                        except Exception as e:
                            Log.error(f"⚠️ [Driver] Failed to fetch ASTF TG stats via RPC: {e}")
                    # =========================================================

                    # Телеметрию пушим уже после обогащения объекта (хорошая практика SSoT)
                    if hasattr(self, 'telemetry'):
                        self.telemetry.push_astf(final_stats, getattr(self, 'ports', []))
                    
                    final_stats['custom_peak_bps'] = session_peak_bps

                    # 🟢 СБРОС ФИНАЛЬНОЙ ТЕЛЕМЕТРИИ В JSON
                    log_name_base = getattr(self.telemetry, 'run_id', 'unknown_run')
                    
                    session_id = os.environ.get("PMI_RUN_ID", "unknown_session")
                    log_dir = os.path.join(SharedConfig.get('paths.logs', '/opt/pmi/logs'), session_id)
                    os.makedirs(log_dir, exist_ok=True)
                    
                    # Формируем имя файла
                    stats_filename = f"stats_{log_name_base}.json"
                    
                    with open(os.path.join(log_dir, stats_filename), 'w', encoding='utf-8') as f:
                        json.dump(final_stats, f, indent=2)
                        
                    Log.info(f"📊 [Telemetry] Final ASTF stats dumped to {stats_filename}")
                except Exception as e: 
                    Log.error(f"⚠️ Failed to dump final JSON telemetry: {e}")
            
            if hasattr(self, 'telemetry'):
                 self.telemetry.stop()
                 
            c.disconnect()
            Log.success("TRex ASTF test finished gracefully.")

        except Exception as e:
            Log.error(f"Execution Error: {e}")
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