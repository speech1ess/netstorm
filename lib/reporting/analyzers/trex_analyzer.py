# -*- coding: utf-8 -*-
import json
from pathlib import Path
from typing import Dict, List, Any, Union

try:
    from pmi_logger import Log
except ImportError:
    import logging
    Log = logging.getLogger(__name__)

class TRexRunAnalyzer:
    """
    Data-Driven парсер JSON-телеметрии.
    Поддерживает как Stateful (ASTF), так и Stateless (STL) форматы.
    """
    def __init__(self, json_path: Union[str, Path]):
        self.filepath = Path(json_path)
        self.data = self._safe_load()
        
        # Определяем режим работы на основе ключей в JSON
        self.is_astf = bool(self.data and "traffic" in self.data and "client" in self.data["traffic"])
        self.is_stl = bool(self.data and not self.is_astf and "total" in self.data and "tx_pkts" in self.data)

    def _safe_load(self) -> Dict[str, Any]:
        if not self.filepath.exists():
            Log.warning(f"[TRexAnalyzer] Артефакт не найден: {self.filepath.name}")
            return {}
            
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError as e:
            Log.error(f"[TRexAnalyzer] Поврежденный JSON {self.filepath.name}: {e}")
            return {}
        except Exception as e:
            Log.error(f"[TRexAnalyzer] I/O Ошибка при чтении {self.filepath.name}: {e}")
            return {}

    @property
    def is_valid(self) -> bool:
        """
        Файл валиден, если он распознан либо как полноценный ASTF, либо как STL.
        """
        return self.is_astf or self.is_stl

    def get_latency_series(self, port: str = "0") -> Dict[str, List[Any]]:
        """Генерирует датасет для ECharts. Только для ASTF, в STL возвращает пустоту."""
        if not self.is_astf:
            return {"x_usec": [], "y_count": []}

        try:
            port_data = self.data.get('latency', {}).get(port, {})
            hist_data = port_data.get('hist', {}).get('histogram', [])
            
            x_usec = []
            y_count = []
            
            for item in hist_data:
                val = int(item['val'])
                if val > 0: 
                    x_usec.append(str(item['key']))
                    y_count.append(val)
                    
            return {"x_usec": x_usec, "y_count": y_count}
        except Exception as e:
            Log.warning(f"[TRexAnalyzer] Ошибка сборки гистограммы: {e}")
            return {"x_usec": [], "y_count": []}

    def get_kpi_summary(self) -> Dict[str, Any]:
        """
        Единая унифицированная точка сборки KPI.
        Всегда возвращает полный набор ключей, чтобы не ломать парсеры.
        """
        # Дефолтный скелет (чтобы Оркестратор никогда не падал с KeyError)
        kpi = {
            "max_tx_bps": 0, "max_tx_pps": 0,
            "l2_tx_frames": 0, "l2_rx_frames": 0, "l2_drops": 0, "l2_drop_pct": 0.0,
            "l7_tx_flows": 0, "l7_rx_flows": 0, "l7_drops": 0, "l7_drop_pct": 0.0,
            "avg_latency_ms": 0, "jitter_usec": 0,
            "ips_blocks": 0, "malware_sent": 0
        }

        if not self.is_valid:
            return kpi

        try:
            # 🟢 1. ГЛОБАЛЬНЫЕ ПИКИ (Общие для всех)
            if 'custom_peak_bps' in self.data:
                kpi["max_tx_bps"] = float(self.data['custom_peak_bps'])
            else:
                kpi["max_tx_bps"] = self.data.get('total', {}).get('tx_bps_L1', 0)
            
            kpi["max_tx_pps"] = self.data.get('total', {}).get('tx_pps', 0)

            total = self.data.get('total', {})

            # 🟢 2. ЖЕЛЕЗНЫЕ МЕТРИКИ L2 (СУРОВАЯ ФИЗИКА - ОБЩЕЕ ДЛЯ ASTF И STL)
            # Читаем счетчики непосредственно с ASIC/DPDK сетевой карты
            l2_tx = self.data.get('tx_pkts') or total.get('opackets', 0)
            l2_rx = self.data.get('rx_pkts') or total.get('ipackets', 0)
            
            kpi["l2_tx_frames"] = l2_tx
            kpi["l2_rx_frames"] = l2_rx
            kpi["l2_drops"] = max(0, l2_tx - l2_rx)
            kpi["l2_drop_pct"] = (kpi["l2_drops"] / l2_tx * 100.0) if l2_tx > 0 else 0.0

            # 🟢 3. ПРИКЛАДНЫЕ МЕТРИКИ L7 (СЕССИИ - ТОЛЬКО ДЛЯ ASTF)
            if self.is_astf:
                client = self.data.get('traffic', {}).get('client', {})
                server = self.data.get('traffic', {}).get('server', {})
                tg_names = client.get('tg_names', {})

                if 'legit' in tg_names:
                    lc = tg_names['legit'].get('client', {})
                    ls = tg_names['legit'].get('server', {})
                    
                    kpi["l7_tx_flows"] = lc.get('tcps_connattempt', 0) + lc.get('udps_sndpkt', 0)
                    kpi["l7_rx_flows"] = lc.get('tcps_connects', 0)
                    
                    # 🟢 ИСТИННЫЙ ПОДСЧЕТ ДРОПОВ (TCP)
                    legit_tcp_drops = lc.get('tcps_drops', 0)
                    
                    # 🟢 ИСТИННЫЙ ПОДСЧЕТ ДРОПОВ (UDP - Двусторонний!)
                    # Потери запросов (Клиент -> Сервер)
                    udp_c2s_drops = max(0, lc.get('udps_sndpkt', 0) - ls.get('udps_rcvpkt', 0))
                    # Потери ответов (Сервер -> Клиент)
                    udp_s2c_drops = max(0, ls.get('udps_sndpkt', 0) - lc.get('udps_rcvpkt', 0))
                    
                    legit_udp_drops = udp_c2s_drops + udp_s2c_drops
                    kpi["l7_drops"] = legit_tcp_drops + legit_udp_drops

                    if 'malware' in tg_names:
                        mc = tg_names['malware'].get('client', {})
                        ms = tg_names['malware'].get('server', {})
                        
                        m_tx_tcp = mc.get('tcps_connattempt', 0)
                        m_tx_udp = mc.get('udps_sndpkt', 0)
                        
                        m_drops_tcp = mc.get('tcps_drops', 0) + mc.get('tcps_testdrops', 0)
                        
                        # Двусторонняя проверка для малвари (иногда эксплойты ждут ответа)
                        m_udp_c2s_drops = max(0, m_tx_udp - ms.get('udps_rcvpkt', 0))
                        m_udp_s2c_drops = max(0, ms.get('udps_sndpkt', 0) - mc.get('udps_rcvpkt', 0))
                        
                        kpi["ips_blocks"] = m_drops_tcp + m_udp_c2s_drops + m_udp_s2c_drops
                        kpi["malware_sent"] = m_tx_tcp + m_tx_udp
                else:
                    # Фоллбэк для старых профилей без тегов (Двусторонний)
                    kpi["l7_tx_flows"] = client.get('tcps_connattempt', 0) + client.get('udps_sndpkt', 0)
                    kpi["l7_rx_flows"] = client.get('tcps_connects', 0)
                    
                    udp_c2s = max(0, client.get('udps_sndpkt', 0) - server.get('udps_rcvpkt', 0))
                    udp_s2c = max(0, server.get('udps_sndpkt', 0) - client.get('udps_rcvpkt', 0))
                    
                    kpi["l7_drops"] = client.get('tcps_drops', 0) + udp_c2s + udp_s2c

                # Защита от деления на ноль и калькуляция процента
                if kpi["l7_tx_flows"] > 0:
                    kpi["l7_drop_pct"] = (kpi["l7_drops"] / kpi["l7_tx_flows"]) * 100.0
                else:
                    kpi["l7_drop_pct"] = 0.0

                kpi["l7_drop_pct"] = min(100.0, kpi["l7_drop_pct"])

                # Latency
                lat_stats = self.data.get('latency', {}).get('0', {})
                kpi["avg_latency_ms"] = round(lat_stats.get('hist', {}).get('s_avg', 0) / 1000, 2)
                kpi["jitter_usec"] = lat_stats.get('stats', {}).get('m_jitter', 0)

            return kpi

        except Exception as e:
            Log.error(f"[TRexAnalyzer] Сбой агрегации KPI: {e}")
            return kpi