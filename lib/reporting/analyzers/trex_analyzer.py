# -*- coding: utf-8 -*-
import json
from pathlib import Path
from typing import Dict, List, Any, Union

# Интегрируем наш логгер. Если выносим класс в отдельный пакет, 
# лучше использовать локальный импорт или стандартный logging
try:
    from pmi_logger import Log
except ImportError:
    import logging
    Log = logging.getLogger(__name__)

class TRexRunAnalyzer:
    """
    Data-Driven парсер ASTF JSON-телеметрии.
    Изолирует шаблонизатор Jinja и стратегию от грязной структуры сырого JSON.
    """
    def __init__(self, json_path: Union[str, Path]):
        self.filepath = Path(json_path)
        self.data = self._safe_load()

    def _safe_load(self) -> Dict[str, Any]:
        """Безопасное чтение JSON с диска. Обрабатываем I/O блокировки и битые файлы."""
        if not self.filepath.exists():
            Log.warning(f"[TRexAnalyzer] Артефакт не найден: {self.filepath.name}")
            return {}
            
        try:
            # Прагматичный совет: если JSON-ы станут огромными (сотни МБ), 
            # здесь имеет смысл переехать на ujson или orjson для снижения CPU-оверхеда
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
        Строгая валидация. TRex мог крашнуться и записать только 'global',
        но нам для аналитики критически нужны блоки latency и traffic.
        """
        return bool(self.data and "latency" in self.data and "traffic" in self.data)

    def get_latency_series(self, port: str = "0") -> Dict[str, List[Any]]:
        """Генерирует датасет для ECharts, отсекая пустые корзины."""
        if not self.is_valid:
            return {"x_usec": [], "y_count": []}

        try:
            port_data = self.data.get('latency', {}).get(port, {})
            hist_data = port_data.get('hist', {}).get('histogram', [])
            
            x_usec = []
            y_count = []
            
            for item in hist_data:
                val = int(item['val'])
                if val > 0: # 🟢 Отсекаем пустые бакеты (убираем "забор")
                    x_usec.append(str(item['key']))
                    y_count.append(val)
                    
            return {"x_usec": x_usec, "y_count": y_count}
        except Exception as e:
            Log.warning(f"[TRexAnalyzer] Ошибка сборки гистограммы: {e}")
            return {"x_usec": [], "y_count": []}

    def get_kpi_summary(self) -> Dict[str, Any]:
        """
        Сводные метрики (Data Aggregation).
        Разделяет легитимные потери (NDR) и заблокированную мальварь (Security Efficacy),
        используя ASTF Template Groups (tg_names).
        """
        if not self.is_valid:
            return {"drops_total": 0, "ips_blocks": 0, "malware_sent": 0, "avg_latency_ms": 0, "jitter_usec": 0, "max_tx_bps": 0}

        try:
            client = self.data.get('traffic', {}).get('client', {})
            server = self.data.get('traffic', {}).get('server', {})
            
            # 🟢 DATA-DRIVEN: Ищем разбивку по Template Groups (tg_names)
            client_tg = client.get('tg_names', {})
            server_tg = server.get('tg_names', {})

            if 'legit' in client_tg:
                # 🛡️ Идеальный сценарий: Берем потери ТОЛЬКО из легитимной группы
                legit_c = client_tg['legit']
                legit_s = server_tg.get('legit', {})
                
                tcp_drops = legit_c.get('tcps_drops', 0) + legit_s.get('tcps_drops', 0)
                udp_drops = legit_c.get('udps_drops', 0) + legit_s.get('udps_drops', 0)
                
                # Фолбэк на разницу пакетов, если аппаратные дропы не отработали
                if tcp_drops == 0 and udp_drops == 0:
                    tx_l7 = legit_c.get('tcps_sndpack', 0) + legit_c.get('udps_sndpkt', 0) + \
                            legit_s.get('tcps_sndpack', 0) + legit_s.get('udps_sndpkt', 0)
                    rx_l7 = legit_c.get('tcps_rcvpack', 0) + legit_c.get('udps_rcvpkt', 0) + \
                            legit_s.get('tcps_rcvpack', 0) + legit_s.get('udps_rcvpkt', 0)
                    drops_total = max(0, tx_l7 - rx_l7)
                else:
                    drops_total = tcp_drops + udp_drops

                # 🦠 Security Efficacy: Считаем, сколько мальвари заблокировал IPS
                malware_c = client_tg.get('malware', {})
                ips_blocks = malware_c.get('tcps_drops', 0) + malware_c.get('udps_drops', 0)
                malware_sent = malware_c.get('tcps_connattempt', 0) + malware_c.get('udps_sndpkt', 0)
                
            else:
                # ⚠️ Фолбэк на общую статистику (для старых логов без tg_name)
                tcp_drops = client.get('tcps_drops', 0) + server.get('tcps_drops', 0)
                udp_drops = client.get('udps_drops', 0) + server.get('udps_drops', 0)
                
                if tcp_drops == 0 and udp_drops == 0:
                    tx_l7 = client.get('tcps_sndpack', 0) + client.get('udps_sndpkt', 0) + \
                            server.get('tcps_sndpack', 0) + server.get('udps_sndpkt', 0)
                    rx_l7 = client.get('tcps_rcvpack', 0) + client.get('udps_rcvpkt', 0) + \
                            server.get('tcps_rcvpack', 0) + server.get('udps_rcvpkt', 0)
                    drops_total = max(0, tx_l7 - rx_l7)
                else:
                    drops_total = tcp_drops + udp_drops
                    
                ips_blocks = 0
                malware_sent = 0

            # 🟢 ЧТЕНИЕ ЧЕСТНОГО ПИКА (Инъекция из Раннера)
            if 'custom_peak_bps' in self.data:
                tx_bps_calculated = float(self.data['custom_peak_bps'])
            else:
                # Фолбэк на хвост, если это старый лог без инъекции
                tx_bps_calculated = self.data.get('total', {}).get('tx_bps_L1', 0)

            lat_stats = self.data.get('latency', {}).get('0', {})
            avg_lat_usec = lat_stats.get('hist', {}).get('s_avg', 0)
            jitter_usec = lat_stats.get('stats', {}).get('m_jitter', 0)

            return {
                "drops_total": drops_total,    # Только легитимные потери (или общие для fallback)
                "ips_blocks": ips_blocks,      # Сколько мальвари убито
                "malware_sent": malware_sent,  # Сколько мальвари отправлено
                "avg_latency_ms": round(avg_lat_usec / 1000, 2),
                "jitter_usec": jitter_usec,
                "max_tx_bps": tx_bps_calculated
            }
        except Exception as e:
            Log.error(f"[TRexAnalyzer] Сбой агрегации KPI: {e}")
            return {"drops_total": 0, "ips_blocks": 0, "malware_sent": 0, "avg_latency_ms": 0, "jitter_usec": 0, "max_tx_bps": 0}