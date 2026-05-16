#!/usr/bin/env python3
import requests
import datetime

vm_url = "http://10.207.129.23:8428"
session_id = "20260515_145336"

print(f"🔍 Ищем все метрики для сессии: {session_id} в {vm_url}...\n")

# Вычисляем окно времени (как мы это делали в стратегии)
session_dt = datetime.datetime.strptime(session_id, "%Y%m%d_%H%M%S")
start_ts = int(session_dt.timestamp()) - 300
end_ts = int(session_dt.timestamp()) + (12 * 3600)

url = f"{vm_url}/api/v1/series"
params = {
    'match[]': f'{{session="{session_id}"}}',
    'start': start_ts,
    'end': end_ts
}

try:
    response = requests.get(url, params=params, timeout=10)
    response.raise_for_status()
    data = response.json().get('data', [])
    
    if not data:
        print("❌ Метрики не найдены в заданном окне времени.")
    else:
        metric_names = set()
        for series in data:
            if '__name__' in series:
                metric_names.add(series['__name__'])
        
        print("🟢 НАЙДЕНЫ СЛЕДУЮЩИЕ МЕТРИКИ:")
        for name in sorted(metric_names):
            print(f"  👉 {name}")
            
        print(f"\nВсего уникальных метрик: {len(metric_names)}")
        
except Exception as e:
    print(f"⚠️ Ошибка: {e}")