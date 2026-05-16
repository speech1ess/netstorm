#!/usr/bin/env python3
import time
import json
from datetime import datetime

# Подтягиваем наш модуль
from reporting.chart_builder import ZabbixClient

# ==========================================
# 🔧 НАСТРОЙКИ (Впиши свои реальные данные)
# ==========================================
ZABBIX_URL = "http://10.207.87.13/" # Или http://ip (без /zabbix)
ZABBIX_TOKEN = "ba77f0cfabc7f48fc51d354dce3f763a281673a8293f57a9c39ab5a88bcc9c79"
TARGET_TAG = "ActiveDUT" # Тег от сетевиков

# Таймфрейм для теста (последние 1 час)
end_ts = int(time.time())
start_ts = end_ts - 3600

print(f"🔌 Подключаемся к Zabbix API: {ZABBIX_URL}...")
zb = ZabbixClient(api_url=ZABBIX_URL, token=ZABBIX_TOKEN)

# 1. Ищем хосты по тегу (НОВЫЙ МЕТОД)
print(f"\n🔍 Ищем хосты с тегом [{TARGET_TAG}]...")
hosts = zb.get_hosts_by_tag(TARGET_TAG)

if not hosts:
    print(f"❌ Хосты с тегом '{TARGET_TAG}' не найдены!")
    exit(1)

print(f"✅ Найдено хостов: {len(hosts)}")
for h in hosts:
    ips = [iface['ip'] for iface in h.get('interfaces', []) if 'ip' in iface]
    print(f"  👉 ID: {h['hostid']} | Имя: {h['name']} | IP: {', '.join(ips)}")

# 2. Выгружаем ключи
test_keys = ["system.cpu.util", "system.ram.util"]

for h in hosts:
    print("\n" + "="*50)
    print(f"📈 Выгрузка метрик для узла: {h['name']} (ID: {h['hostid']})")
    print("="*50)
    
    for key in test_keys:
        t, v = zb.get_item_history(h['hostid'], key, start_ts, end_ts)
        if t:
            print(f"🟢 [ {key} ] УСПЕХ! Точек: {len(v)}. Последнее значение: {v[-1]}%")
        else:
            print(f"❌ [ {key} ] Данные не найдены (Либо ключ неверный, либо данных за час нет).")