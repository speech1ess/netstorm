#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Dynamic index generator for PMI test results (Compact Table Dashboard).
Scans /opt/pmi/results/, parses HTML reports for metrics, and generates index.html.
"""

import os
import sys
import re
import json
from datetime import datetime

try:
    from shared import SharedConfig, Log
except ImportError:
    SharedConfig = None
    Log = None

def _log_info(msg):
    if Log: Log.info(f"INDEX: {msg}")

def _log_error(msg):
    if Log: Log.error(f"INDEX: {msg}")

# ─────────────────────────────────────────────────────────────
# EMBEDDED HTML TEMPLATES (Стиль: Компактные таблицы)
# ─────────────────────────────────────────────────────────────

BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PMI Test Registry</title>
    <style>
        :root {{ --bg: #f4f6f9; --card-bg: #ffffff; --text: #2c3e50; --border: #e1e8ed; --primary: #3498db; --success: #2ecc71; --fail: #e74c3c; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: var(--bg); color: var(--text); padding: 20px; }}
        .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; background: var(--card-bg); padding: 20px 30px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }}
        .header h1 {{ margin: 0; font-size: 24px; }}
        .stats {{ color: #7f8c8d; font-size: 14px; }}
        
        .day-group {{ margin-bottom: 30px; }}
        .day-header {{ font-size: 18px; font-weight: bold; margin-bottom: 15px; color: #34495e; border-bottom: 2px solid var(--primary); padding-bottom: 5px; display: inline-block; }}
        .weekday {{ color: #95a5a6; font-size: 14px; font-weight: normal; margin-left: 10px; }}
        
        table {{ width: 100%; border-collapse: collapse; background: var(--card-bg); border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.05); }}
        th, td {{ padding: 12px 15px; text-align: left; border-bottom: 1px solid var(--border); }}
        th {{ background: #f8f9fa; font-size: 12px; text-transform: uppercase; color: #7f8c8d; letter-spacing: 0.5px; }}
        tr:last-child td {{ border-bottom: none; }}
        tr:hover {{ background: #fdfefe; }}
        
        .col-id {{ width: 140px; font-family: monospace; font-size: 12px; color: #95a5a6; }}
        .col-dut {{ width: 200px; font-weight: 600; color: #2c3e50; }}
        .col-scenario {{ font-weight: 500; }}
        .col-sec {{ width: 80px; text-align: center; font-size: 12px; font-weight: bold; }}
        .col-result {{ font-weight: bold; color: #2c3e50; width: 120px; }}
        .col-status {{ width: 60px; text-align: center; font-size: 18px; }}
        .col-actions {{ width: 120px; text-align: right; }}
        
        .btn {{ display: inline-block; padding: 6px 12px; border-radius: 4px; text-decoration: none; font-size: 13px; font-weight: 500; transition: background 0.2s; }}
        .btn-report {{ background: var(--primary); color: white; }}
        .btn-report:hover {{ background: #2980b9; }}
        .btn-log {{ background: #95a5a6; color: white; margin-left: 5px; }}
        .btn-log:hover {{ background: #7f8c8d; }}
        
        .text-success {{ color: var(--success); }}
        .text-fail {{ color: var(--fail); }}
        .text-muted {{ color: #95a5a6; font-weight: normal; font-size: 13px; }}
        
        .badge-on {{ color: #e74c3c; }}
        .badge-off {{ color: #bdc3c7; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>PMI Test Registry</h1>
        <div class="stats">{total_runs} sessions recorded | Updated: {generated_ts}</div>
    </div>
    {body}
</body>
</html>
"""

DAY_GROUP_TEMPLATE = """
<div class="day-group">
    <div class="day-header">{day_label} <span class="weekday">{weekday}</span></div>
    <table>
        <thead>
            <tr>
                <th class="col-id">Session ID</th>
                <th class="col-dut">DUT</th>
                <th class="col-status">St</th>
                <th class="col-scenario">Scenario</th>
                <th class="col-sec">IPS/AV</th>
                <th class="col-result">Peak Load</th>
                <th class="col-actions">Actions</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
</div>
"""

TABLE_ROW_TEMPLATE = """
<tr>
    <td class="col-id">{dir_name}</td>
    <td class="col-dut">{dut_name}</td>
    <td class="col-status" title="{status_text}">{status_icon}</td>
    <td class="col-scenario">{scenario_name}</td>
    <td class="col-sec">{sec_badge}</td>
    <td class="col-result">{main_metric}</td>
    <td class="col-actions">{links_html}</td>
</tr>
"""

LINK_TEMPLATE = '<a href="{path}" class="btn {style}" target="_blank">{icon}</a>'

# ─────────────────────────────────────────────────────────────
# LOGIC & EXTRACTION
# ─────────────────────────────────────────────────────────────

def get_results_dir():
    if SharedConfig: return SharedConfig.get('paths.results', "/opt/pmi/results")
    return "/opt/pmi/results"

def parse_dir_datetime(dir_name):
    m = re.match(r'(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})', dir_name)
    if not m: return None
    year, month, day, hour, minute, second = map(int, m.groups())
    return datetime(year, month, day, hour, minute, second)

def extract_run_info(dir_path, dir_name, has_report):
    """Извлекаем данные из session_meta.json и парсим HTML-отчет"""
    info = {
        'scenario_name': 'Unknown Scenario',
        'main_metric': '-',
        'status_icon': '⚪',
        'status_text': 'No Report',
        'dut_name': 'Unknown DUT',
        'sec_badge': '<span class="badge-off" title="No Security Profile">⭕ OFF</span>'
    }

    # 1. Читаем DTO из папки LOGS (а не results!)
    logs_dir = SharedConfig.get('paths.logs', "/opt/pmi/logs") if SharedConfig else "/opt/pmi/logs"
    meta_path = os.path.join(logs_dir, dir_name, 'session_meta.json')
    
    if os.path.exists(meta_path):
        try:
            with open(meta_path, 'r', encoding='utf-8') as f:
                meta = json.load(f)
                if meta.get('dut_label'):
                    info['dut_name'] = re.sub(r'\s*\[.*?\]', '', meta['dut_label']).strip()
        except Exception as e:
            _log_error(f"Error reading session_meta.json in {dir_name}: {e}")

    report_path = os.path.join(dir_path, f"report_{dir_name}.html")
    if not has_report or not os.path.exists(report_path):
        return info

    try:
        with open(report_path, 'r', encoding='utf-8') as f:
            html = f.read()

        # Ищем название сценария
        scen_match = re.search(r'\[([A-Z0-9_]+)\]', html)
        if scen_match:
            info['scenario_name'] = scen_match.group(1)
            
        full_scen_match = re.search(r'\|\s*([^<]+?)\s*<', html)
        if full_scen_match:
            clean_name = full_scen_match.group(1).replace('\n', '').strip()
            if clean_name and "Матрица" not in clean_name:
                info['scenario_name'] = f"{clean_name} <span class='text-muted'>[{info['scenario_name']}]</span>"

        # Детект IPS: Ищем строгий маркер включения (inject_malware: 1), а не просто упоминание в артефактах
        if re.search(r'("inject_malware"\s*:\s*1|\'inject_malware\'\s*:\s*1)', html, re.IGNORECASE):
            info['sec_badge'] = '<span class="badge-on" title="Malware Validation Active">🛡️ ON</span>'

        # Статус (Pass/Fail/Limit)
        if "✅ Pass" in html or "LIMIT FOUND" in html or "Validated Non-Drop Rate" in html:
            info['status_icon'] = '🟢'
            info['status_text'] = 'Success / Limit Found'
        elif "💀" in html or "Fail" in html or "Drops:" in html:
            info['status_icon'] = '🔴'
            info['status_text'] = 'Failed / Drops Detected'
        else:
            info['status_icon'] = '🟡'

        # Главная метрика (Пиковая нагрузка)
        metric_match = re.search(r'>\s*([\d\.\s]+(?:Gbps|Mbps|M CC|K CC|CC|Mpps|Kpps|pps|M CPS|K CPS|CPS))\s*<', html, re.IGNORECASE)
        if metric_match:
            info['main_metric'] = metric_match.group(1).strip()
            
    except Exception as e:
        _log_error(f"Error parsing HTML {report_path}: {e}")

    return info

def get_test_runs():
    results_dir = get_results_dir()
    runs = []
    if not os.path.exists(results_dir): return []

    try:
        dirs = sorted([d for d in os.listdir(results_dir) if os.path.isdir(os.path.join(results_dir, d)) and re.match(r'\d{8}_\d{6}', d)], reverse=True)
    except: return []

    for dir_name in dirs:
        dir_path = os.path.join(results_dir, dir_name)
        dt = parse_dir_datetime(dir_name)
        
        links = []
        has_report = False
        report_file = f"report_{dir_name}.html"
        
        if os.path.exists(os.path.join(dir_path, report_file)):
            links.append({'name': 'Report', 'icon': '📊', 'path': f"{dir_name}/{report_file}", 'style': 'btn-report'})
            has_report = True
            
        if os.path.exists(os.path.join(dir_path, "pmi_session.log")):
            links.append({'name': 'Log', 'icon': '📄', 'path': f"{dir_name}/pmi_session.log", 'style': 'btn-log'})

        if links:
            meta = extract_run_info(dir_path, dir_name, has_report)
            
            runs.append({
                'dir_name': dir_name,
                'day_key': dt.strftime("%Y-%m-%d") if dt else "unknown",
                'day_label': dt.strftime("%d %b %Y") if dt else "Unknown Date",
                'weekday': dt.strftime("%A") if dt else "",
                'links': links,
                'meta': meta
            })
            
    return runs

def generate_html():
    runs = get_test_runs()
    by_day = {}
    for r in runs: by_day.setdefault(r['day_key'], []).append(r)
        
    sorted_days = sorted(by_day.keys(), reverse=True)
    body_parts = []
    
    if not runs:
        body_parts.append("<div style='padding: 30px; text-align: center; color: #7f8c8d;'>No test sessions recorded yet.</div>")
    else:
        for day in sorted_days:
            day_runs = by_day[day]
            first = day_runs[0]
            
            rows_html = ""
            for r in day_runs:
                links_html = "".join([LINK_TEMPLATE.format(**lnk) for lnk in r['links']])
                
                rows_html += TABLE_ROW_TEMPLATE.format(
                    dir_name=r['dir_name'],
                    dut_name=r['meta']['dut_name'],
                    status_icon=r['meta']['status_icon'],
                    status_text=r['meta']['status_text'],
                    scenario_name=r['meta']['scenario_name'],
                    sec_badge=r['meta']['sec_badge'],
                    main_metric=r['meta']['main_metric'],
                    links_html=links_html
                )
            
            body_parts.append(DAY_GROUP_TEMPLATE.format(
                day_label=first['day_label'],
                weekday=first['weekday'],
                rows_html=rows_html
            ))

    return BASE_TEMPLATE.format(total_runs=len(runs), body="".join(body_parts), generated_ts=datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

if __name__ == '__main__':
    if '--generate' in sys.argv:
        out_file = os.path.join(get_results_dir(), 'index.html')
        try:
            with open(out_file, 'w', encoding='utf-8') as f: f.write(generate_html())
            print(f"Dashboard updated: {out_file}")
        except Exception as e: print(f"Error: {e}")
    else:
        print(generate_html())