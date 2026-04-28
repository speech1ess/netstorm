#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate Consolidated Session Report from PMI session log.
Parses logs/<session_id>/pmi_session.log -> results/<session_id>/report_<id>.html
"""

import os
import re
import sys
import shutil
import subprocess
from datetime import datetime

pmi_lib = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if pmi_lib not in sys.path:
    sys.path.insert(0, pmi_lib)

try:
    # Теперь мы импортируем "сверху вниз", как нормальные люди
    from reporting.html_templates import SESSION_REPORT_TEMPLATE
    from shared import SharedConfig
    from pmi_logger import Log
except ImportError as e:
    # Если даже так не нашлось — значит, файлы реально удалили
    print(f"[ERROR] Critical reporting imports failed: {e}")
    
    # Минимальные фоллбэки, чтобы скрипт не вылетел с NameError
    SESSION_REPORT_TEMPLATE = "<html><body><h1>Report Error</h1><p>Template not found</p></body></html>"
    SharedConfig = None
    
    # Создаем "пустой" логгер, чтобы вызовы Log.info() не крашили скрипт
    class DummyLog:
        @staticmethod
        def info(m): print(f"[INFO] {m}")
        @staticmethod
        def error(m): print(f"[ERR] {m}")
        @staticmethod
        def warning(m): print(f"[WARN] {m}")
        @staticmethod
        def success(m): print(f"[OK] {m}")
    Log = DummyLog

PMI_LOG_NAME = "pmi_session.log"

# ─────────────────────────────────────────────────────────────
# REGEX PATTERNS (RELAXED)
# ─────────────────────────────────────────────────────────────
# Разрешаем опциональное время в квадратных скобках или без них
RE_SESSION_START = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+=== ORCHESTRATOR START: (?P<label>.+) \((?P<type>.*)\) ===')
RE_ITERATION_START = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+Execution started\. Duration: (?P<dur>[\d\.]+)s')

# Spawn больше не требует Tput/Mult в конце строки
RE_ACTOR_SPAWN = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+Spawn:\s+(?P<tool>\w+)\s+->\s+(?P<log>\S+)(?:.*\(Tput/Mult:\s+(?P<load>[\d\.\?]+)/(?P<mult>[\d\.\?]+)\))?')

# 🟢 ВЕРНУЛИ НА БАЗУ (Его менять не надо, JMeter пишет стабильно):
RE_JMETER_SUM = re.compile(r'summary =\s+(?P<count>\d+)\s+in\s+\S+\s+=\s+(?P<rate>[\d\.]+)/s.*Err:\s+(?P<err>\d+)')

RE_ITERATION_END = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+(Iteration execution finished|Orchestrator finished)\.')
RE_MANUAL_TREX = re.compile(r'INFO\s+TREX START: (?P<profile>.+) \(ID: (?P<id>[^\)]+)\)')
RE_MANUAL_JMETER = re.compile(r'INFO\s+JMETER START: (?P<profile>.+) \(ID: (?P<id>[^\)]+)\)')

# ─────────────────────────────────────────────────────────────
# HELPER FUNCTIONS
# ─────────────────────────────────────────────────────────────
def get_actor_stats_from_log(tool, log_path):
    """Parses individual tool logs for RPS/Errors, Latency and TRex TX/RX"""
    stats = {'rps': 0.0, 'errors': 0, 'total': 0, 'raw_summary': '-', 'rx_pps': 0.0, 'avg_rt': '-', 'max_rt': '-'}
    if not os.path.exists(log_path): return stats
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                line = line.strip()
                if tool == 'JMETER' and 'summary =' in line:
                    parts = line.split('summary =')
                    if len(parts) > 1:
                        raw = "summary =" + parts[1]
                        stats['raw_summary'] = raw
                        
                        # Обновленная регулярка: ищем Avg, Max и Err
                        m = re.search(r'=\s+(?P<rate>[\d\.]+)/s.*Avg:\s+(?P<avg>\d+).*Max:\s+(?P<max>\d+).*Err:\s+(?P<err>\d+)', raw)
                        if m:
                            stats['rps'] = float(m.group('rate'))
                            stats['avg_rt'] = m.group('avg')
                            stats['max_rt'] = m.group('max')
                            stats['errors'] = int(m.group('err'))
                            try: stats['total'] = int(parts[1].strip().split()[0])
                            except: pass
                
                elif tool == 'TREX':
                    if 'Total-Tx' in line: stats['raw_summary'] = line
                    elif 'TX:' in line and 'RX:' in line:
                        # СТАРЫЙ ПАРСИНГ STL
                        stats['raw_summary'] = line
                        m_time = re.search(r'\[\s*(?P<sec>\d+)s\]', line)
                        curr_sec = int(m_time.group('sec')) if m_time else None

                        val_tx = 0.0
                        val_bps = 0.0 
                        
                        m_tx = re.search(r'TX:\s+(?P<pps>[\d\.]+)(?P<mult>[kM]?)\s*pps\s*\((?P<bps>[\d\.]+)(?P<bmult>[kKMG]?)bps\)', line)
                        if m_tx:
                            val_tx = float(m_tx.group('pps'))
                            if m_tx.group('mult') == 'M': val_tx *= 1000000
                            elif m_tx.group('mult') == 'k': val_tx *= 1000
                            
                            val_bps = float(m_tx.group('bps'))
                            bmult = m_tx.group('bmult').upper()
                            if bmult == 'G': val_bps *= 1_000_000_000
                            elif bmult == 'M': val_bps *= 1_000_000
                            elif bmult == 'K': val_bps *= 1_000
                        
                        val_rx = 0.0
                        m_rx = re.search(r'RX:\s+(?P<pps>[\d\.]+)(?P<mult>[kM]?)\s*pps', line)
                        if m_rx:
                            val_rx = float(m_rx.group('pps'))
                            if m_rx.group('mult') == 'M': val_rx *= 1000000
                            elif m_rx.group('mult') == 'k': val_rx *= 1000

                        # Интеграция STL
                        if curr_sec is not None:
                            if 'last_sec' not in stats:
                                stats['last_sec'] = 0
                                stats['total_tx_pkts'] = 0.0
                                stats['total_rx_pkts'] = 0.0
                                stats['total_tx_bits'] = 0.0
                            
                            delta_t = curr_sec - stats['last_sec']
                            if delta_t > 0:
                                stats['total_tx_pkts'] += val_tx * delta_t
                                stats['total_rx_pkts'] += val_rx * delta_t
                                stats['total_tx_bits'] += val_bps * delta_t
                                stats['last_sec'] = curr_sec
                            
                            if stats['last_sec'] > 0:
                                stats['rps'] = stats['total_tx_pkts'] / stats['last_sec']
                                stats['rx_pps'] = stats['total_rx_pkts'] / stats['last_sec']
                                stats['avg_bps'] = stats['total_tx_bits'] / stats['last_sec']
                        else:
                            stats['rps'] = val_tx
                            stats['rx_pps'] = val_rx
                            stats['avg_bps'] = val_bps

                    elif 'ASTF Active Flows' in line:
                        # 🟢 НОВЫЙ ПАРСИНГ ДЛЯ ASTF
                        stats['raw_summary'] = line
                        m_time = re.search(r'\[\s*(?P<sec>\d+)s\]', line)
                        curr_sec = int(m_time.group('sec')) if m_time else None
                        
                        val_bps = 0.0
                        m_tx = re.search(r'TX:\s+(?P<bps>[\d\.]+)(?P<bmult>[kMGT]?)bps', line)
                        if m_tx:
                            val_bps = float(m_tx.group('bps'))
                            bmult = m_tx.group('bmult').upper()
                            if bmult == 'G': val_bps *= 1_000_000_000
                            elif bmult == 'M': val_bps *= 1_000_000
                            elif bmult == 'K': val_bps *= 1_000
                            
                        # Для ASTF у нас нет PPS, будем использовать bps как основную метрику
                        if curr_sec is not None:
                            if 'last_sec' not in stats:
                                stats['last_sec'] = 0
                                stats['total_tx_bits'] = 0.0
                                
                            delta_t = curr_sec - stats['last_sec']
                            if delta_t > 0:
                                stats['total_tx_bits'] += val_bps * delta_t
                                stats['last_sec'] = curr_sec
                                
                            if stats['last_sec'] > 0:
                                stats['avg_bps'] = stats['total_tx_bits'] / stats['last_sec']
                                # Хак: записываем bps в rps, чтобы логика таблицы не сломалась
                                stats['rps'] = stats['avg_bps'] / 1000 # Примерный пересчет для графиков
                                stats['rx_pps'] = stats['rps'] # У ASTF нет разницы TX/RX пакетов в логе
                        else:
                            stats['avg_bps'] = val_bps
                            stats['rps'] = val_bps / 1000
    except: pass
    return stats

def read_and_clean_session_log(log_path):
    """Reads pmi_session.log and removes ANSI colors"""
    if not os.path.exists(log_path): return "Log file not found."
    
    clean_lines = []
    # Regex to strip ANSI escape codes
    ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
    
    try:
        with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                clean_text = ansi_escape.sub('', line)
                clean_lines.append(clean_text)
    except Exception as e:
        return f"Error reading log: {e}"
        
    return "".join(clean_lines)

def format_session_label(raw_label, session_data=None):
    """
    Умный парсер заголовков.
    Пример IN: ICMP Flood [L3_ICMP] + Baseline (Low (3m: 2000 Mult))
    Пример OUT: ("[L3_ICMP]", "ICMP Flood + Baseline | Low Load: 3min: 400 RPS Base & 2 Mpps")
    """
    import re

    m_params = re.search(r'\((Low|Medium|High)\s*\((.*?)\)\)$', raw_label, re.IGNORECASE)
    
    if m_params:
        level = m_params.group(1).strip()
        details = m_params.group(2).strip()
        title_part = raw_label[:m_params.start()].strip()
    else:
        level = ""
        details = ""
        title_part = raw_label

    tag = ""
    m_tag = re.search(r'\[(.*?)\]', title_part)
    if m_tag:
        tag = f"[{m_tag.group(1)}]"
        title_part = title_part.replace(m_tag.group(0), '')

    desc = ""
    m_desc = re.search(r'\((.*?)\)', title_part)
    if m_desc:
        desc = f"({m_desc.group(1)})"
        title_part = title_part.replace(m_desc.group(0), '')

    clean_title = re.sub(r'\s+', ' ', title_part).strip()
    clean_title = re.sub(r'^\+\s*|\s*\+$', '', clean_title).strip()

    final_label = f"{tag} {desc}".strip()
    if not final_label:
        final_label = clean_title

    if level and details:
        # --- [НОВОЕ] Достаем RPS из таблицы, если его нет в заголовке ---
        if "RPS" not in details and session_data:
            jmeter_load = None
            try:
                # Ищем нагрузку JMeter в первой итерации
                for a in session_data['iterations'][0]['actors']:
                    if a['tool'] == 'JMETER' and a.get('load') and a['load'] != '?':
                        jmeter_load = a['load']
                        break
            except Exception:
                pass
                
            if jmeter_load:
                # Вставляем RPS сразу после времени (например, после 3m:)
                if re.match(r'^\d+m:', details):
                    details = re.sub(r'^(\d+m:)\s*', fr'\1 {jmeter_load} RPS Base, ', details)
                else:
                    details = f"{jmeter_load} RPS Base, {details}"
        # -----------------------------------------------------------------

        details = re.sub(r'(\d+)m:', r'\1min / ', details)
        
        def mult_repl(m):
            val = int(m.group(1))
            if val >= 1000:
                return f"{val/1000:g} Mpps"
            else:
                return f"{val}k pps"
                
        details = re.sub(r'(\d+)\s*Mult', mult_repl, details)
        details = details.replace(',', ' &')
        
        if final_label != clean_title:
            final_subtitle = f"{clean_title} | {level} Load: {details}"
        else:
            final_subtitle = f"{level} Load: {details}"
    else:
        final_subtitle = "" if final_label == clean_title else clean_title

    return final_label, final_subtitle

# ─────────────────────────────────────────────────────────────
# PARSING LOGIC
# ─────────────────────────────────────────────────────────────
def parse_session_log(log_path):
    print(f"[DEBUG] Parsing log: {log_path}")
    
    session = {
        'label': 'Manual / Single Run',
        'type': 'Single',
        'start': '?',
        'end': '?',
        'iterations': []
    }
    
    current_iter = None
    is_orchestrator = False

    def ensure_iter(start_time, dur=0):
        nonlocal current_iter
        if current_iter is None:
            current_iter = {
                'id': len(session['iterations']) + 1,
                'start': start_time,
                'duration': dur,
                'actors': [],
                'avg_rps': 0
            }
            session['iterations'].append(current_iter)
        return current_iter

    with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            if not line: continue
            
            # 1. Start Session
            m_sess = RE_SESSION_START.search(line)
            if m_sess:
                session['label'] = m_sess.group('label').strip()
                session['type'] = m_sess.group('type').strip()
                session['start'] = m_sess.group('time')
                is_orchestrator = True
                continue

            # 2. Iteration Start
            m_iter = RE_ITERATION_START.search(line)
            if m_iter:
                current_iter = None
                ensure_iter(m_iter.group('time'), float(m_iter.group('dur')))
                is_orchestrator = True
                continue

            # 3. Manual Run
            if not is_orchestrator:
                m_man_trex = RE_MANUAL_TREX.search(line)
                if m_man_trex:
                    t = line[:8] if len(line) >= 8 else "?"
                    it = ensure_iter(t)
                    session['label'] = f"TRex: {m_man_trex.group('profile')}"
                    log_file = f"{m_man_trex.group('id')}.log"
                    it['actors'].append({
                        'start': t, 'tool': 'TREX', 'log': log_file, 'load': '?', 'mult': '?',
                        'artifacts': [{'name': 'TRex Console', 'link': log_file, 'style': 'btn-console'}]
                    })
                    continue

                m_man_jm = RE_MANUAL_JMETER.search(line)
                if m_man_jm:
                    t = line[:8] if len(line) >= 8 else "?"
                    it = ensure_iter(t)
                    session['label'] = f"JMeter: {m_man_jm.group('profile')}"
                    run_id = m_man_jm.group('id')
                    log_file = f"{run_id}_internal.log"
                    artifacts = [
                        {'name': 'JMeter Console', 'link': log_file, 'style': 'btn-console'},
                        {'name': 'Raw JTL', 'link': f"{run_id}.jtl", 'style': 'btn'},
                        {'name': 'HTML Report', 'link': f"{run_id}_report/index.html", 'style': 'btn-primary'}
                    ]
                    it['actors'].append({
                        'start': t, 'tool': 'JMETER', 'log': log_file, 'load': '?', 'mult': '?', 'artifacts': artifacts
                    })
                    continue

            # 4. Actor Spawn    
            m_act = RE_ACTOR_SPAWN.search(line)
            if m_act:
                # В нашей новой регулярке время может быть None
                spawn_time = m_act.group('time') if m_act.group('time') else '00:00:00'
                
                it = ensure_iter(spawn_time)
                
                tool = m_act.group('tool')
                log_file = m_act.group('log')
                if any(a['log'] == log_file for a in it['actors']): continue

                prof_name = log_file.replace('.log', '')
                if prof_name.lower().startswith(tool.lower() + '_'):
                    prof_name = prof_name[len(tool)+1:]
                prof_name = re.sub(r'(_run\d+)?_\d{6}$', '', prof_name)

                artifacts = []
                if tool == 'JMETER':
                    base = log_file.replace('.log', '')
                    artifacts.append({'name': 'JMeter Console', 'link': log_file, 'style': 'btn-console'})
                    artifacts.append({'name': 'Raw JTL', 'link': base + '.jtl', 'style': 'btn'})
                    artifacts.append({'name': 'HTML Report', 'link': base + '_report/index.html', 'style': 'btn-primary'})
                elif tool == 'TREX':
                    artifacts.append({'name': 'TRex Console', 'link': log_file, 'style': 'btn-console'})

                it['actors'].append({
                    'start': spawn_time,
                    'tool': tool,
                    'log': log_file,
                    'profile': prof_name,
                    'load': m_act.group('load') if m_act.group('load') else '?', # 🟢 Фолбэк, если не нашли
                    'mult': m_act.group('mult') if m_act.group('mult') else '?', # 🟢 Фолбэк, если не нашли
                    'artifacts': artifacts
                })
                continue

            # 5. End
            m_end = RE_ITERATION_END.search(line)
            if m_end:
                current_iter['end'] = m_end.group('time')
                current_iter = None

    if current_iter:
        if not current_iter.get('end'): current_iter['end'] = 'In Progress/Killed'
        
    if session['iterations']:
        session['end'] = session['iterations'][-1].get('end', '?')
        
    return session


# ─────────────────────────────────────────────────────────────
# HTML GENERATION
# ─────────────────────────────────────────────────────────────
def generate_html(session_id, data, logs_root, results_root):
    overview_rows = ""
    artifacts_section_html = "" # New Section
    target_health_html = ""     # New Section for Chart
    
    total_rps_accum = 0
    valid_rps_count = 0
    total_tests = 0 # New Counter
    
    out_dir = os.path.join(results_root, session_id)
    os.makedirs(out_dir, exist_ok=True)

    row_counter = 0
    
    for it in data['iterations']:
        
        # --- BUILDING OVERVIEW ROWS PER ACTOR ---
        iter_artifacts_inner = ""
        
        for a in it['actors']:
            row_counter += 1
            total_tests += 1
            
            # 1. Copy Files Logic
            src_log = os.path.join(logs_root, session_id, a['log'])
            dst_log = os.path.join(out_dir, a['log'])
            if os.path.exists(src_log): shutil.copy2(src_log, dst_log)
            
            if a['tool'] == 'JMETER':
                base = a['log'].replace('.log', '')
                src_jtl = os.path.join(logs_root, session_id, base + '.jtl')
                dst_jtl = os.path.join(out_dir, base + '.jtl')
                if os.path.exists(src_jtl): shutil.copy2(src_jtl, dst_jtl)
                src_rep_dir = os.path.join(logs_root, session_id, base + '_report')
                dst_rep_dir = os.path.join(out_dir, base + '_report')
                if os.path.exists(src_rep_dir) and not os.path.exists(dst_rep_dir):
                    try: shutil.copytree(src_rep_dir, dst_rep_dir)
                    except: pass

            # 2. Get Stats
            stats = get_actor_stats_from_log(a['tool'], dst_log)
            a['raw_summary'] = stats['raw_summary']

            actual_rps = stats['rps']
            errors = stats['errors']
            total = stats['total']
            
            if actual_rps > 0 and a['tool'] != 'TREX':
                total_rps_accum += actual_rps
                valid_rps_count += 1

            # 3. Form Row Data
            load_config = ""
            rps_display = ""
            err_display = "-"
            
            p_name = a.get('profile', 'unknown').upper()
            
            # --- УМНАЯ ЛОГИКА СТАТУСОВ ---
            is_baseline = 'BASELINE' in p_name
            status_txt = "UNKNOWN"
            status_cls = "status-fail"
            err_style = "color:#ccc;"

            if a['tool'] == 'JMETER':
                load_config = f"<b>JMETER</b>: {a['load']} RPS"
                target_str = a['load']
                rps_display = f"{actual_rps:,.1f}"
                if target_str and target_str != '?': rps_display += f" / {target_str}"
                
                error_rate = (errors / total * 100) if total > 0 else (100.0 if errors > 0 else 0.0)
                #err_display = f"{errors} ({error_rate:.2f}%)"

                if is_baseline:
                    # Для Baseline ошибки — это ложные срабатывания (плохо)
                    if error_rate > 0.1:
                        status_txt, status_cls = "FAIL", "status-fail"
                        err_style = "color:#e74c3c; font-weight:bold;"
                        err_display = f"{errors} ({error_rate:.2f}%) > 0.1%"
                    else:
                        status_txt, status_cls = "PASS", "status-pass"
                        err_display = f"{errors} ({error_rate:.2f}%) < 0.1%"
                else:
                    # Для Attack ошибки — это блокировка (ХОРОШО!)
                    if error_rate >= 98.0:
                        status_txt, status_cls = "BLOCKED", "status-blocked" # 🔵 ИСПРАВЛЕНО НА СИНИЙ
                        err_style = "color:#2980b9; font-weight:bold;"      # Текст процента тоже синий
                        err_display = f"{errors} ({error_rate:.2f}%) >= 98%"
                    elif error_rate > 10.0:
                        status_txt, status_cls = "PARTIAL", "status-warning"
                        err_style = "color:#f39c12; font-weight:bold;"
                        err_display = f"{errors} ({error_rate:.2f}%) > 10%"
                    else:
                        status_txt, status_cls = "BYPASSED", "status-fail"
                        err_style = "color:#e74c3c; font-weight:bold;"
                        err_display = f"{errors} ({error_rate:.2f}%) < 10%"

            elif a['tool'] == 'TREX':
                try:
                    mult_val = float(a['mult'])
                    raw_pps = mult_val * 1000  # Наша базовая ставка из скрипта (1000 pps)
                    
                    if raw_pps >= 1_000_000:
                        # Формат :g сам уберет лишние нули (2.0 станет 2)
                        load_config = f"<b>TREX</b>: {raw_pps / 1_000_000:g} Mpps"
                    elif raw_pps >= 1000:
                        load_config = f"<b>TREX</b>: {raw_pps / 1000:g} Kpps"
                    else:
                        load_config = f"<b>TREX</b>: {raw_pps:g} pps"
                except (ValueError, TypeError):
                    # Фолбэк, если там знак вопроса (например, при Ad-Hoc тесте)
                    load_config = f"<b>TREX</b>: {a.get('mult', '?')}x1000 pps"
                
                rx_pps = min(stats.get('rx_pps', 0.0), actual_rps)
                
                # --- [НОВОЕ] Красивое форматирование полосы пропускания ---
                avg_bps = stats.get('avg_bps', 0.0)
                if avg_bps >= 1_000_000_000:
                    bw_display = f"{avg_bps / 1_000_000_000:.1f} Gbps"
                elif avg_bps >= 1_000_000:
                    bw_display = f"{avg_bps / 1_000_000:.1f} Mbps"
                elif avg_bps >= 1_000:
                    bw_display = f"{avg_bps / 1_000:.1f} Kbps"
                else:
                    bw_display = f"{avg_bps:.0f} bps"

                # Добавляем Avg BW под метриками PPS
                rps_display = f"TX: {actual_rps:,.0f} pps<br>RX: {rx_pps:,.0f} pps<br><span style='font-size:0.85em; color:#888;'>(Avg BW: {bw_display})</span>"
                
                if actual_rps > 0:
                    leakage_rate = min((rx_pps / actual_rps) * 100.0, 100.0)
                    err_display = f"Leakage: {leakage_rate:.2f}% < 2%" # Твои новые пороги :)
                    
                    if leakage_rate > 2.0:
                        status_txt, status_cls = "LEAK", "status-fail"
                        err_style = "color:#e74c3c; font-weight:bold;"
                    else:
                        status_txt, status_cls = "BLOCKED", "status-blocked"
                        err_style = "color:#2980b9; font-weight:bold;"
                else:
                    status_txt, status_cls, err_display = "NO TX", "status-fail", "0 TX"
                    err_style = "color:#e74c3c; font-weight:bold;"

            # --- КОСМЕТИКА: ВЫДЕЛЕНИЕ БЕЙСЛАЙНА ---
            start_time_display = a.get('start', it['start'])
            row_style = ""
            display_name = f"<b>{p_name}</b>"
            
            if is_baseline:
                # Жирная полоса снизу и легкий серый фон
                row_style = 'style="border-bottom: 3px solid #7f8c8d; background-color: #f8f9fa;"'
                display_name = f"⭐ <b>{p_name}</b>"

            rt_display = "-"
            if a['tool'] == 'JMETER' and stats.get('avg_rt') != '-':
                rt_display = f"{stats['avg_rt']} ms<br><span style='font-size:0.85em; color:#888;'>(Max: {stats['max_rt']})</span>"
            elif a['tool'] == 'TREX':
                rt_display = "<span style='color:#555;'>N/A</span>"

            overview_rows += f"""
            <tr {row_style}>
                <td>{display_name}</td>
                <td>{start_time_display}</td>
                <td>{it.get('duration', '?')}s</td>
                <td>{load_config}</td>
                <td>{rps_display}</td>
                <td>{rt_display}</td> 
                <td style="{err_style}">{err_display}</td>
                <td><span class="{status_cls}">{status_txt}</span></td>
            </tr>
            """
            
            # 4. Collect Artifacts Buttons
            btns = ""
            for art in a['artifacts']:
                cls = art.get('style', 'btn')
                btns += f'<a href="{art["link"]}" class="btn {cls}" target="_blank">{art["name"]}</a> '
            
            iter_artifacts_inner += f"""
            <div style="margin-bottom:10px; border-bottom:1px solid #eee; padding-bottom:10px;">
                <div style="font-weight:bold; color:#555; margin-bottom:5px; font-size:13px;">
                    <span style="color:#2980b9;">{a['tool']}</span> - {a['log']}
                </div>
                <div style="display:flex; gap:10px;">{btns}</div>
            </div>
            """

        # Wrap artifacts in card per iteration
        artifacts_section_html += f"""
        <div class="iter-card">
            <div class="iter-header"><span class="iter-title">Iteration #{it['id']} Artifacts</span></div>
            <div class="iter-body">{iter_artifacts_inner}</div>
        </div>
        """

    # --- TARGET METRICS CHART SECTION ---
    src_csv = os.path.join(logs_root, session_id, "target_metrics.csv")
    if os.path.exists(src_csv):
        # 1. Copy CSV to results
        dst_csv = os.path.join(out_dir, "target_metrics.csv")
        shutil.copy2(src_csv, dst_csv)
        
        # 2. Generate Chart HTML
        try:
            from chart_builder import build_target_chart_html
            target_health_html = build_target_chart_html(dst_csv)
        except ImportError:
            pass

    # --- SESSION LOG SECTION ---
    session_log_path = os.path.join(logs_root, session_id, "pmi_session.log")
    cleaned_log = read_and_clean_session_log(session_log_path)
    
    log_section_html = f"""
    <div class="iter-card">
        <div class="iter-header"><span class="iter-title">Full Session Log</span></div>
        <div class="iter-body" style="padding:0;">
            <pre class="log-view">{cleaned_log}</pre>
        </div>
    </div>
    """

    avg_session_rps = round(total_rps_accum / valid_rps_count, 1) if valid_rps_count > 0 else 0
    total_duration_str = "~"
    try:
        t1 = datetime.strptime(data['start'], "%H:%M:%S")
        t2 = datetime.strptime(data['end'], "%H:%M:%S")
        total_duration_str = str(t2 - t1)
    except: pass

    fancy_title, fancy_subtitle = format_session_label(data['label'], data)

    return SESSION_REPORT_TEMPLATE.format(
        session_id=session_id,
        label=fancy_title,
        subtitle=fancy_subtitle,
        start_time=data['start'],
        total_duration=total_duration_str,
        run_count=len(data['iterations']),
        total_tests=total_tests,
        avg_rps=avg_session_rps,
        overview_rows=overview_rows,
        artifacts_section=artifacts_section_html,
        target_health_section=target_health_html,
        log_section=log_section_html,
        gen_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    if len(sys.argv) < 2:
        session_id = os.environ.get("PMI_RUN_ID")
        if not session_id:
            print(f"Usage: {sys.argv[0]} <session_id>")
            sys.exit(1)
    else:
        session_id = sys.argv[1]

    if SharedConfig:
        logs_root = SharedConfig.get('paths.logs', '/opt/pmi/logs')
        results_root = SharedConfig.get('paths.results', '/opt/pmi/results')
    else:
        logs_root = '/opt/pmi/logs'
        results_root = '/opt/pmi/results'

    pmi_log = os.path.join(logs_root, session_id, PMI_LOG_NAME)
    
    if not os.path.exists(pmi_log):
        err = f"Error: Log file not found: {pmi_log}"
        if Log: Log.error(err)
        print(err)
        sys.exit(1)

    if Log: Log.info(f"Generating report for session {session_id}...")
    
    try:
        data = parse_session_log(pmi_log)
        
        if not data['iterations']:
            print("Warning: No iterations parsed (Strict mode).")

        html = generate_html(session_id, data, logs_root, results_root)
        
        out_dir = os.path.join(results_root, session_id)
        os.makedirs(out_dir, exist_ok=True)
        
        out_path = os.path.join(out_dir, f"report_{session_id}.html")
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
            
        msg = f"Report generated: {out_path}"
        if Log: Log.success(msg)
        print(msg)

        # Update Index
        index_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'generate_index.py')
        if os.path.exists(index_script):
            subprocess.run([sys.executable, index_script, '--generate'], check=False)
        
    except Exception as e:
        err = f"Failed to generate report: {e}"
        if Log: Log.error(err)
        print(err)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()