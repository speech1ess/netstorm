#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Модуль сборки данных для графиков ECharts.
Реализует паттерны Adapter (для работы с API баз данных) 
и Builder (для формирования готовых датасетов под конкретные графики).
"""

import requests
from typing import List, Tuple, Dict, Any, Optional
from pmi_logger import Log  # 🟢 Подключили православный логгер

# ==========================================
# 🛑 ЛЕГАСИ-ЗАГЛУШКА
# ==========================================
def build_target_chart_html(csv_path: str) -> str:
    """
    Устарело: раньше тут был рендер графиков через Chart.js из локальных CSV.
    Оставлено исключительно для обратной совместимости, чтобы не ломать 
    старые стратегии (например, DDoS), которые всё ещё пытаются это импортировать.
    
    :param csv_path: Путь к старому target_metrics.csv.
    :return: Всегда пустая строка.
    """
    return ""

# ==========================================
# 🔌 АДАПТЕРЫ БАЗ ДАННЫХ (Data Sources)
# ==========================================
class VictoriaMetricsClient:
    """
    Адаптер для работы с VictoriaMetrics (совместим с Prometheus API).
    Выполняет PromQL запросы и форматирует ответы для JS-фронтенда.
    """
    
    def __init__(self, base_url: str):
        """
        Инициализация клиента.
        
        :param base_url: Базовый URL сервера VictoriaMetrics (например, 'http://10.0.0.1:8428').
        """
        self.base_url = base_url.rstrip('/')

    def query_range(self, query: str, start_ts: int, end_ts: int, step: str = "1s") -> Tuple[List[int], List[float]]:
        """
        Выполняет запрос query_range к VictoriaMetrics.
        
        :param query: Строка PromQL запроса.
        :param start_ts: Unix timestamp начала выборки.
        :param end_ts: Unix timestamp конца выборки.
        :param step: Шаг агрегации (по умолчанию "1s").
        :return: Кортеж из двух списков: (timestamps_ms, values).
                 Таймстемпы отдаются в миллисекундах для нативной совместимости с JS (ECharts).
        """
        url = f"{self.base_url}/api/v1/query_range"
        params = {
            'query': query, 
            'start': start_ts, 
            'end': end_ts, 
            'step': step
        }
        try:
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            data = response.json().get('data', {}).get('result', [])
            
            if not data:
                return [], []
            
            # ECharts любит таймстемпы в миллисекундах
            timestamps = [int(float(val[0]) * 1000) for val in data[0]['values']]
            values = [round(float(val[1]), 2) for val in data[0]['values']]
            return timestamps, values
            
        except Exception as e:
            Log.error(f"[VictoriaMetricsClient] Ошибка запроса [{query}]: {e}")
            return [], []

class ZabbixClient:
    """
    Адаптер для работы с Zabbix API (JSON-RPC).
    """
    def __init__(self, api_url: str, token: str):
        self.api_url = api_url.rstrip('/')
        if not self.api_url.endswith('api_jsonrpc.php'):
            self.api_url = f"{self.api_url}/api_jsonrpc.php"
        self.token = token
        self.req_id = 0

    def _call(self, method: str, params: Dict[str, Any]) -> Any:
        """Внутренний метод для отправки JSON-RPC запросов"""
        self.req_id += 1
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "auth": self.token,
            "id": self.req_id
        }
        try:
            resp = requests.post(self.api_url, json=payload, timeout=10)
            resp.raise_for_status()
            result = resp.json()
            if 'error' in result:
                Log.error(f"[ZabbixClient] API Error: {result['error']}")
                return None
            return result.get('result')
        except Exception as e:
            Log.error(f"[ZabbixClient] Network Error: {e}")
            return None

    def get_hostid_by_ip(self, ip: str) -> Optional[str]:
        """Ищет внутренний ID хоста по его IP"""
        interfaces = self._call("hostinterface.get", {"filter": {"ip": ip}, "output": ["hostid"]})
        if interfaces:
            return interfaces[0]['hostid']
        return None

    # 🟢 ВОТ ЭТОТ НОВЫЙ МЕТОД МЫ ДОБАВИЛИ ДЛЯ ПОИСКА ПО ТЕГАМ:
    def get_hosts_by_tag(self, tag_name: str) -> list:
        """Ищет список хостов по тегу Zabbix"""
        params = {
            "output": ["hostid", "name"],
            "tags": [{"tag": tag_name}],
            "selectInterfaces": ["ip"]
        }
        hosts = self._call("host.get", params)
        return hosts if hosts else []

    def get_item_history(self, host_id: str, item_key: str, start_ts: int, end_ts: int) -> Tuple[List[int], List[float]]:
        """Получает исторические данные метрики"""
        # 1. Сначала находим itemid и тип данных метрики (value_type)
        items = self._call("item.get", {
            "hostids": host_id,
            "search": {"key_": item_key},
            "output": ["itemid", "value_type"]
        })
        
        if not items:
            Log.warning(f"[ZabbixClient] Метрика {item_key} не найдена для хоста {host_id}")
            return [], []
            
        item_id = items[0]['itemid']
        value_type = int(items[0]['value_type']) # 0=Float, 3=Unsigned
        
        # 2. Запрашиваем историю
        history = self._call("history.get", {
            "output": "extend",
            "history": value_type,
            "itemids": [item_id],
            "time_from": start_ts,
            "time_till": end_ts,
            "sortfield": "clock",
            "sortorder": "ASC"
        })
        
        if not history:
            return [], []
            
        # Zabbix отдает время в секундах, переводим в миллисекунды для ECharts
        timestamps = [int(point['clock']) * 1000 for point in history]
        values = [round(float(point['value']), 2) for point in history]
        
        return timestamps, values

# ==========================================
# 📊 СБОРЩИК ДАННЫХ ДЛЯ ГРАФИКОВ (ECharts Builder)
# ==========================================
class ChartDataBuilder:
    """
    Строитель структур данных для графиков ECharts.
    Скрывает под капотом PromQL/Zabbix запросы и отдает готовые словари, 
    которые можно напрямую сериализовать в JSON для фронтенда.
    """
    
    def __init__(self, vm_url: Optional[str] = None, zabbix_url: Optional[str] = None, zabbix_token: Optional[str] = None):
        """
        Инициализация билдера и его адаптеров.
        
        :param vm_url: URL для подключения к VictoriaMetrics.
        :param zabbix_url: URL для подключения к Zabbix.
        """
        self.vm = VictoriaMetricsClient(vm_url) if vm_url else None
        self.zabbix = ZabbixClient(zabbix_url, zabbix_token) if zabbix_url and zabbix_token else None

    def build_throughput_chart(self, session_id: str, start_ts: int, end_ts: int) -> Dict[str, list]:
        """
        Собирает данные для графика пропускной способности (L2/L7 Throughput).
        Извлекает скорости TX (отправлено) и RX (принято) в Гбит/с.
        
        :param session_id: Уникальный идентификатор сессии (для фильтрации метрик).
        :param start_ts: Время начала теста (Unix timestamp).
        :param end_ts: Время конца теста (Unix timestamp).
        :return: Словарь с осями {'time': [...], 'tx': [...], 'rx': [...]}.
        """
        if not self.vm:
            return {"time": [], "tx": [], "rx": []}

        # 🟢 ДОБАВЛЕН sum() чтобы склеить все перезапуски TRex в одну линию!
        query_tx = f'sum(netstorm_astf_tx_bps{{session="{session_id}"}}) / 1e9'
        query_rx = f'sum(netstorm_astf_rx_bps{{session="{session_id}"}}) / 1e9'
        
        t_tx, v_tx = self.vm.query_range(query_tx, start_ts, end_ts)
        t_rx, v_rx = self.vm.query_range(query_rx, start_ts, end_ts)
        
        return {
            "time": t_tx if t_tx else t_rx,
            "tx": v_tx,
            "rx": v_rx
        }

    def build_flow_state_chart(self, session_id: str, start_ts: int, end_ts: int) -> Dict[str, list]:
        """Новый график: Active Flows vs CPS (как в Grafana)"""
        if not self.vm:
            return {"time": [], "active_flows": [], "cps": []}

        # Вытаскиваем стейт-машину
        query_flows = f'sum(netstorm_astf_active_flows{{session="{session_id}"}})'
        query_cps = f'sum(netstorm_astf_cps{{session="{session_id}"}})'
        
        t_flows, v_flows = self.vm.query_range(query_flows, start_ts, end_ts)
        t_cps, v_cps = self.vm.query_range(query_cps, start_ts, end_ts)
        
        return {
            "time": t_flows if t_flows else t_cps,
            "active_flows": v_flows,
            "cps": v_cps
        }

    def build_target_health_chart(self, tag_name: str, cpu_key: str, ram_key: str, start_ts: int, end_ts: int) -> dict:
        """Собирает график CPU/RAM кластера (все узлы по тегу)"""
        if not getattr(self, 'zabbix', None):
            return {"time": [], "hosts": {}}
            
        hosts = self.zabbix.get_hosts_by_tag(tag_name)
        if not hosts:
            Log.error(f"[ChartDataBuilder] Zabbix не нашел хосты по тегу {tag_name}")
            return {"time": [], "hosts": {}}

        result = {"time": [], "hosts": {}}
        global_time = []

        for h in hosts:
            name = h['name']
            t_cpu, v_cpu = self.zabbix.get_item_history(h['hostid'], cpu_key, start_ts, end_ts)
            t_ram, v_ram = self.zabbix.get_item_history(h['hostid'], ram_key, start_ts, end_ts)
            
            if not global_time and t_cpu:
                global_time = t_cpu
            elif not global_time and t_ram:
                global_time = t_ram
                
            result["hosts"][name] = {"cpu": v_cpu, "ram": v_ram}

        result["time"] = global_time
        return result