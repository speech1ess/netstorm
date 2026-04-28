#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic index generator for PMI test results (Dashboard).
Scans /opt/pmi/results/ and generates index.html with calendar view.
"""

import os
import sys
import re
from datetime import datetime

# Подключаем шаблоны
try:
    sys.path.append(os.path.dirname(os.path.abspath(__file__)))
    from shared import SharedConfig, Log
    # Импортируем ВСЕ шаблоны для индекса
    from html_templates import (
        BASE_TEMPLATE, 
        DAY_CARD_TEMPLATE, 
        RUN_CARD_TEMPLATE,
        FILE_LINK_TEMPLATE, 
        EMPTY_BODY_TEMPLATE
    )
except ImportError:
    # Fallback (если запускаем локально или нет shared)
    from html_templates import (
        BASE_TEMPLATE, 
        DAY_CARD_TEMPLATE, 
        RUN_CARD_TEMPLATE,
        FILE_LINK_TEMPLATE, 
        EMPTY_BODY_TEMPLATE
    )
    SharedConfig = None
    Log = None

def _log_info(msg):
    if Log: Log.info(f"INDEX: {msg}")

def _log_error(msg):
    if Log: Log.error(f"INDEX: {msg}")

# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def get_results_dir():
    if SharedConfig:
        return SharedConfig.get('paths.results', "/opt/pmi/results")
    return "/opt/pmi/results"

def parse_dir_datetime(dir_name):
    """Парсит имя папки YYYYMMDD_HHMMSS -> datetime object"""
    m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', dir_name)
    if not m: return None
    year, month, day, hour, minute, second = map(int, m.groups())
    return datetime(year, month, day, hour, minute, second)

def time_ago(dt):
    """Возвращает строку '2h ago', '5m ago'"""
    if not dt: return ""
    diff = (datetime.now() - dt).total_seconds()
    if diff < 60: return f"{int(diff)}s ago"
    elif diff < 3600: return f"{int(diff/60)}m ago"
    elif diff < 86400: return f"{int(diff/3600)}h ago"
    else: return f"{int(diff/86400)}d ago"

# ─────────────────────────────────────────────────────────────
# SCANNING LOGIC
# ─────────────────────────────────────────────────────────────

def get_test_runs():
    results_dir = get_results_dir()
    runs = []
    
    if not os.path.exists(results_dir):
        return []

    # 1. Получаем список папок-сессий, сортируем от новых к старым
    try:
        dirs = sorted([d for d in os.listdir(results_dir) 
                       if os.path.isdir(os.path.join(results_dir, d)) 
                       and re.match(r'\d{8}_\d{6}', d)], 
                      reverse=True)
    except Exception as e:
        _log_error(f"Scan failed: {e}")
        return []

    for dir_name in dirs:
        dir_path = os.path.join(results_dir, dir_name)
        dt = parse_dir_datetime(dir_name)
        
        links = []
        has_main_report = False

        # А. Ищем ГЛАВНЫЙ отчет сессии (report_SESSIONID.html)
        # Это тот файл, который генерирует generate_run_summary.py
        report_file = f"report_{dir_name}.html"
        if os.path.exists(os.path.join(dir_path, report_file)):
            links.append({
                'name': '📊 Open Report',
                'path': f"{dir_name}/{report_file}",
                'style': 'btn-primary' # Синяя/Зеленая кнопка
            })
            has_main_report = True
        
        # Б. Ищем лог сессии (как запасной вариант или доп. инфо)
        if os.path.exists(os.path.join(dir_path, "pmi_session.log")):
             links.append({
                'name': 'Session Log',
                'path': f"{dir_name}/pmi_session.log",
                'style': 'btn-console' # Темная кнопка
            })

        # Если нашли хоть что-то полезное - добавляем в список раннов
        if links:
            runs.append({
                'dir_name': dir_name,
                'dt': dt,
                'time_label': dt.strftime("%H:%M") if dt else "??:??",
                'ago': time_ago(dt),
                'day_key': dt.strftime("%Y-%m-%d") if dt else "unknown",
                'day_label': dt.strftime("%d %b %Y") if dt else "Unknown Date",
                'weekday': dt.strftime("%A") if dt else "",
                'links': links,
                'has_report': has_main_report
            })
            
    return runs

# ─────────────────────────────────────────────────────────────
# HTML GENERATION
# ─────────────────────────────────────────────────────────────

def generate_html():
    runs = get_test_runs()
    
    # Группировка по дням
    by_day = {}
    for r in runs:
        by_day.setdefault(r['day_key'], []).append(r)
        
    sorted_days = sorted(by_day.keys(), reverse=True)
    
    body_parts = []
    
    if not runs:
        body_parts.append(EMPTY_BODY_TEMPLATE)
    else:
        for day in sorted_days:
            day_runs = by_day[day]
            first = day_runs[0] # Берем первого для заголовка дня
            
            run_cards_html = ""
            for r in day_runs:
                # Генерим HTML кнопок
                links_html = ""
                for link in r['links']:
                    links_html += FILE_LINK_TEMPLATE.format(
                        path=link['path'],
                        name=link['name'],
                        style_class=link.get('style', '')
                    )
                
                # Определяем цвет полоски (Зеленая если есть отчет, Красная/Серая если нет)
                status_class = "has-report" if r['has_report'] else "no-report"
                
                run_cards_html += RUN_CARD_TEMPLATE.format(
                    status_class=status_class,
                    time_label=r['time_label'],
                    ago=r['ago'],
                    dir_name=r['dir_name'],
                    files_html=links_html
                )
            
            # Карточка дня
            weekday_html = f'<span class="weekday-pill">{first["weekday"]}</span>' if first['weekday'] else ''
            
            body_parts.append(DAY_CARD_TEMPLATE.format(
                day_label=first['day_label'],
                weekday_html=weekday_html,
                runs_html=run_cards_html
            ))

    # Сборка финального HTML
    return BASE_TEMPLATE.format(
        total_runs=len(runs),
        body="".join(body_parts),
        generated_ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

# ─────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == '__main__':
    # Если запущен с флагом --generate, пишем в файл
    if '--generate' in sys.argv:
        results_dir = get_results_dir()
        out_file = os.path.join(results_dir, 'index.html')
        try:
            html = generate_html()
            with open(out_file, 'w', encoding='utf-8') as f:
                f.write(html)
            _log_info(f"Dashboard updated: {out_file}")
            print(f"Dashboard updated: {out_file}")
        except Exception as e:
            _log_error(f"Failed to update dashboard: {e}")
            print(f"Error: {e}")
    else:
        # Иначе просто выводим в stdout (для дебага)
        print(generate_html())