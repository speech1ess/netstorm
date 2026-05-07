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
        """Сводные метрики (Data Aggregation) для обоих режимов."""
        if not self.is_valid:
            return {"drops_total": 0, "drop_pct": 0.0, "ips_blocks": 0, "malware_sent": 0, "avg_latency_ms": 0, "jitter_usec": 0, "max_tx_bps": 0, "max_tx_pps": 0}

        try:
            # 🟢 ВЕТКА 1: STATEFUL (ASTF)
            if self.is_astf:
                client = self.data.get('traffic', {}).get('client', {})
                server = self.data.get('traffic', {}).get('server', {})
                
                # Считаем отправленные для ASTF
                tx_pkts_astf = client.get('tcps_connattempt', 0) + client.get('udps_accepts', client.get('udps_sndpkt', 0))
                
                client_tg = client.get('tg_names', {})

                if 'legit' in client_tg:
                    legit_c = client_tg['legit'].get('client', {})
                    legit_s = client_tg['legit'].get('server', {})
                    
                    # 🟢 ВЫРЕЗАЛИ udps_keepdrops! Это нормальное закрытие UDP-сессии по таймауту.
                    drops_total = (
                        legit_c.get('tcps_drops', 0) + legit_c.get('tcps_conndrops', 0) + legit_c.get('tcps_timeoutdrop', 0) + 
                        legit_c.get('udps_drops', 0) + legit_c.get('udps_noportbcast', 0) + legit_c.get('udps_errs', 0) +
                        legit_s.get('tcps_drops', 0) + legit_s.get('tcps_conndrops', 0)
                    )

                    malware_c = client_tg.get('malware', {}).get('client', {})
                    ips_blocks = malware_c.get('tcps_drops', 0) + malware_c.get('tcps_testdrops', 0)
                    malware_sent = malware_c.get('tcps_connattempt', 0) + malware_c.get('udps_sndpkt', 0)
                    
                else:
                    # 🟢 И для старых профилей тоже убираем keepdrops
                    drops_total = (
                        client.get('tcps_drops', 0) + client.get('tcps_conndrops', 0) + client.get('tcps_timeoutdrop', 0) +
                        client.get('udps_drops', 0) + client.get('udps_noportbcast', 0) + client.get('udps_errs', 0) +
                        server.get('tcps_drops', 0) + server.get('tcps_conndrops', 0)
                    )
                        
                    ips_blocks = 0
                    malware_sent = 0

                lat_stats = self.data.get('latency', {}).get('0', {})
                avg_lat_usec = lat_stats.get('hist', {}).get('s_avg', 0)
                jitter_usec = lat_stats.get('stats', {}).get('m_jitter', 0)
                avg_latency_ms = round(avg_lat_usec / 1000, 2)
                
                drop_pct = (drops_total / tx_pkts_astf * 100.0) if tx_pkts_astf > 0 else 0.0

            # 🟢 ВЕТКА 2: STATELESS (STL)
            else:
                total = self.data.get('total', {})
                # ТОТ САМЫЙ ФОЛЛБЭК КАК В sc_utils!
                tx_pkts = self.data.get('tx_pkts') or total.get('opackets', 0)
                rx_pkts = self.data.get('rx_pkts') or total.get('ipackets', 0)
                
                # 🟢 Вот она, математика вычитания! Только для STL!
                drops_total = max(0, tx_pkts - rx_pkts)
                drop_pct = (drops_total / tx_pkts * 100.0) if tx_pkts > 0 else 0.0
                
                ips_blocks = 0
                malware_sent = 0
                avg_latency_ms = 0
                jitter_usec = 0

            # 🟢 ОБЩИЙ БЛОК: ЧТЕНИЕ ПИКА ТРАФИКА
            if 'custom_peak_bps' in self.data:
                tx_bps_calculated = float(self.data['custom_peak_bps'])
            else:
                tx_bps_calculated = self.data.get('total', {}).get('tx_bps_L1', 0)

            # 🟢 ТЯНЕМ PPS ИЗ ТОТАЛА
            tx_pps_calculated = self.data.get('total', {}).get('tx_pps', 0)

            return {
                "drops_total": drops_total,
                "drop_pct": drop_pct,
                "ips_blocks": ips_blocks,
                "malware_sent": malware_sent,
                "avg_latency_ms": avg_latency_ms,
                "jitter_usec": jitter_usec,
                "max_tx_bps": tx_bps_calculated,
                "max_tx_pps": tx_pps_calculated  # ✅ ДОБАВЛЕНО
            }

        except Exception as e:
            Log.error(f"[TRexAnalyzer] Сбой агрегации KPI: {e}")
            # ✅ ДОБАВЛЕН max_tx_pps В EXCEPT
            return {"drops_total": 0, "drop_pct": 0.0, "ips_blocks": 0, "malware_sent": 0, "avg_latency_ms": 0, "jitter_usec": 0, "max_tx_bps": 0, "max_tx_pps": 0}