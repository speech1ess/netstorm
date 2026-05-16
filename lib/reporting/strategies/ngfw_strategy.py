#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import os
import shutil
import json
from datetime import datetime
from pathlib import Path

from pmi_logger import Log
from reporting.analyzers.trex_analyzer import TRexRunAnalyzer
from reporting.html_templates import NGFW_SESSION_REPORT_TEMPLATE
from reporting.strategies.base_strategy import BaseReportStrategy

# 🟢 Отрезаем всё, начиная с первой "пробел-скобки" до конца строки
META_TAG_PATTERN = re.compile(r'\s*\(.*$')

class NGFWReportStrategy(BaseReportStrategy):
    """
    Стратегия для отчетов NGFW.
    Наследует I/O-операции базы. Поддерживает динамический рендер.
    """

    def parse_logs(self):
        data = super().parse_logs()

        malware_logs = set()
        mult_map = {}
        hc_map = {}         # 🟢 Вместо списка делаем словарь
        active_log = None   # 🟢 Трекаем лог текущей итерации

        if os.path.exists(self.session_log_path):
            with open(self.session_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    if 'TRex command generated:' in line:
                        m_cmd = re.search(r'\.py\s+([\d\.]+)\s+\d+\s+([^\s{]+)', line)
                        if m_cmd:
                            mult_val = m_cmd.group(1)
                            log_base = m_cmd.group(2) + '.log' # 🟢 Формируем ключ
                            
                            active_log = log_base
                            mult_map[active_log] = mult_val
                            
                            # 🟢 По умолчанию ставим зеленый Pass для новой итерации
                            hc_map[active_log] = '<span title="CP & DP OK">✅ Pass</span>'
                            
                            if '"inject_malware": 1' in line and m_cmd:
                                malware_logs.add(active_log)

                    # 🟢 Если мы внутри итерации, парсим её Health Check
                    if active_log:
                        if 'WARN_LIMIT' in line and 'Превышен' in line:
                            # Ставим DEGRADED, только если еще не было фатала
                            if 'CP Fail' not in hc_map[active_log] and 'DP Fatal' not in hc_map[active_log]:
                                hc_map[active_log] = '<span title="Data Plane Degraded" style="color:#f39c12; font-weight:bold;">⚠️ Degraded</span>'
                        
                        elif 'Health Check Failed' in line or 'Health Check Error' in line or ('Ping DUT' in line and 'FAILED' in line):
                            # Упал Ping/SSH (Control Plane)
                            hc_map[active_log] = '<span title="Control Plane Dead" style="color:#c0392b; font-weight:bold;">❌ CP Fail</span>'
                        
                        elif 'FATAL:' in line and 'DUT is overwhelmed' in line:
                            # Упал по дропам трафика (Data Plane)
                            hc_map[active_log] = '<span title="Data Plane Overwhelmed" style="color:#e74c3c; font-weight:bold;">💀 DP Fatal</span>'

        for i, it in enumerate(data.get('iterations', [])):
            for a in it.get('actors', []):
                a['is_ips'] = (a['log'] in malware_logs)
                if a['log'] in mult_map:
                    a['mult'] = mult_map[a['log']]
                
                # 🟢 Забираем иконку строго по имени лога, никаких смещений индексов
                a['hc_icon'] = hc_map.get(a['log'], '') 

        return data

    def _load_session_meta(self):
        """
        Ленивая загрузка иммутабельного контекста сессии (DTO).
        Изолирует репортер от глобального стейта оркестратора.
        """
        if hasattr(self, 'session_meta'):
            return self.session_meta

        meta_path = os.path.join(self.logs_root, self.session_id, 'session_meta.json')
        
        # Fallback на случай запуска репортера для старых логов
        self.session_meta = {
            "description": "",
            "dut_label": "Unknown DUT",
            "dut_type": "Unknown",
            "thresholds": {'warn': 0.05, 'fatal': 0.1}
        }

        if os.path.exists(meta_path):
            try:
                with open(meta_path, 'r', encoding='utf-8') as f:
                    loaded_meta = json.load(f)
                    self.session_meta.update(loaded_meta)
            except Exception as e:
                Log.error(f"[{self.__class__.__name__}] Failed to parse session_meta.json: {e}")
        else:
            Log.warning(f"⚠️ [Tech Debt] session_meta.json not found at {meta_path}! Using failsafe defaults.")

        return self.session_meta

    def evaluate_metrics(self, data):
        Log.info(f"[{self.__class__.__name__}] Evaluating metrics and copying artifacts...")
        data['eval_meta'] = {'total_rps_accum': 0, 'valid_rps_count': 0, 'total_tests': 0}

        for it in data.get('iterations', []):
            for a in it.get('actors', []):
                data['eval_meta']['total_tests'] += 1
                
                src_log = os.path.join(self.logs_root, self.session_id, a['log'])
                dst_log = os.path.join(self.out_dir, a['log'])
                if os.path.exists(src_log): 
                    shutil.copy2(src_log, dst_log)
                
                if a['tool'] == 'JMETER':
                    base = a['log'].replace('.log', '')
                    for ext in ['.jtl', '_report']:
                        src = os.path.join(self.logs_root, self.session_id, base + ext)
                        dst = os.path.join(self.out_dir, base + ext)
                        if os.path.exists(src):
                            shutil.copy2(src, dst) if os.path.isfile(src) else shutil.copytree(src, dst, dirs_exist_ok=True)

                stats = self._get_actor_stats_from_log(a['tool'], dst_log)
                actual_rps = stats.get('rps', 0)
                errors = stats.get('errors', 0)
                total = stats.get('total', 0)
                
                eval_data = self._calculate_status(a, stats, actual_rps, errors, total)
                a['eval'] = eval_data
                a['stats'] = stats

        return data

    def _get_actor_stats_from_log(self, tool, log_path):
        stats = {'rps': 0.0, 'errors': 0, 'total': 0, 'raw_summary': '-', 'rx_pps': 0.0, 'avg_rt': '-', 'max_rt': '-', 'astf_drops': 0, 'drop_pct': 0.0}
        
        if tool == 'TREX':
            log_p = Path(log_path)
            json_name = f"stats_{log_p.stem}.json"
            
            source_json = Path(self.logs_root) / self.session_id / json_name
            target_json = Path(self.out_dir) / json_name

            analyzer = TRexRunAnalyzer(str(source_json))
            
            if analyzer.is_valid:
                try:
                    if not target_json.exists():
                        shutil.copy2(source_json, target_json)
                except Exception as e:
                    Log.warning(f"[{self.__class__.__name__}] Failed to copy JSON: {e}")

                # 🟢 ЕДИНЫЙ ИСТОЧНИК ПРАВДЫ (Берем данные из нового Анализатора)
                kpi = analyzer.get_kpi_summary()
                
                raw_bps = kpi.get('max_tx_bps', 0)
                stats['max_tx_bps_raw'] = raw_bps 
                stats['max_tx_bw'] = f"{raw_bps / 1e9:.2f} Gbps" if raw_bps >= 1e9 else f"{raw_bps / 1e6:.2f} Mbps"
                stats['pps'] = kpi.get('max_tx_pps', 0)
                
                # 🟢 УМНЫЙ ВЫБОР ДРОПОВ (Синхронизировано с NetSecOPEN логикой Оркестратора)
                if getattr(analyzer, 'is_astf', False):
                    l2_drop_pct = kpi.get('l2_drop_pct', 0.0)
                    l7_drop_pct = kpi.get('l7_drop_pct', 0.0)
                    L2_TOLERANCE = 1.0
                    
                    if l2_drop_pct >= L2_TOLERANCE:
                        # Если физика сыпется жестко (>1%), показываем в отчете L2
                        stats['tx_pkts'] = kpi.get('l2_tx_frames', 0)
                        stats['astf_drops'] = kpi.get('l2_drops', 0)
                        stats['drop_pct'] = l2_drop_pct
                    else:
                        # Если L2 в пределах нормы, показываем в отчете чистые сессии L7
                        stats['tx_pkts'] = kpi.get('l7_tx_flows', 0)
                        stats['astf_drops'] = kpi.get('l7_drops', 0)
                        stats['drop_pct'] = l7_drop_pct
                else:
                    # Для Stateless всегда показываем физику
                    stats['tx_pkts'] = kpi.get('l2_tx_frames', 0)
                    stats['astf_drops'] = kpi.get('l2_drops', 0)
                    stats['drop_pct'] = kpi.get('l2_drop_pct', 0.0)

                # Malware статистика (если была)
                stats['malware_tx'] = kpi.get('malware_sent', 0)
                stats['malware_drops'] = kpi.get('ips_blocks', 0)

                # Задержки (Latency)
                stats['chart_data'] = analyzer.get_latency_series()
                stats['latency_avg'] = kpi.get('avg_latency_ms', 0)
                stats['latency_ms'] = kpi.get('avg_latency_ms', 0) # Дубликат для совместимости
                stats['jitter'] = kpi.get('jitter_usec', 0)
                
                # 🟢 DATA-DRIVEN: Парсим Time-Series для долгих тестов на емкость (MAX_CC)
                time_series = {'time_s': [], 'active_flows': [], 'drops': []}
                if 'max_cc' in str(log_path).lower():
                    try:
                        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                            for line in f:
                                if 'Active Flows:' in line:
                                    m = re.search(r'\[\s*(\d+)s\].*?Active Flows:\s*(\d+).*?Total Drops:\s*(\d+)', line)
                                    if m:
                                        time_series['time_s'].append(int(m.group(1)))
                                        time_series['active_flows'].append(int(m.group(2)))
                                        time_series['drops'].append(int(m.group(3)))
                    except Exception as e:
                        Log.warning(f"Failed to parse time-series from {log_path}: {e}")
                stats['time_series'] = time_series
            else:
                Log.warning(f"[{self.__class__.__name__}] JSON artifact not found or invalid: {source_json}")
                stats.update({'astf_drops': 0, 'drop_pct': 0.0, 'max_tx_bps_raw': 0, 'max_tx_bw': "0 bps", 'chart_data': {"x_usec": [], "y_count": []}})
                
        elif tool == 'JMETER':
            if not os.path.exists(log_path): return stats
            try:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if 'summary =' in line:
                            parts = line.split('summary =')
                            if len(parts) > 1:
                                raw = "summary =" + parts[1]
                                stats['raw_summary'] = raw
                                m = re.search(r'=\s+(?P<rate>[\d\.]+)/s.*Avg:\s+(?P<avg>\d+).*Max:\s+(?P<max>\d+).*Err:\s+(?P<err>\d+)', raw)
                                if m:
                                    stats.update({'rps': float(m.group('rate')), 'avg_rt': m.group('avg'), 'max_rt': m.group('max'), 'errors': int(m.group('err'))})
                                    try: stats['total'] = int(parts[1].strip().split()[0])
                                    except: pass
            except: pass
            
        return stats

    def _calculate_status(self, actor, stats, actual_rps, errors, total):
        ev = {'status_txt': 'UNKNOWN', 'status_cls': 'status-fail', 'err_style': 'color:#ccc;'}
        p_name = actor.get('profile', 'unknown')
        
        is_baseline = 'BASELINE' in p_name
        is_ips = actor.get('is_ips', False)

        if is_ips:
            ev['display_name'] = f"{p_name}☣️"
            ev['row_style'] = 'style="background-color: #fcf3cf;"' 
        elif is_baseline:
            ev['display_name'] = f"⭐ <b>{p_name.upper()}</b>"
            ev['row_style'] = 'style="border-bottom: 3px solid #7f8c8d; background-color: #f8f9fa;"'
        else:
            ev['display_name'] = f"{p_name}"
            ev['row_style'] = ""

        # 🟢 DATA-DRIVEN: Читаем замороженные лимиты сессии
        meta = self._load_session_meta()
        thresholds = meta.get('thresholds', {})
        warn_limit = float(thresholds.get('warn', 0.05))
        fatal_limit = float(thresholds.get('fatal', 0.1))
        
        ev['hc_icon'] = actor.get('hc_icon', '-')

        # 🟢 ФИКС: Динамическое форматирование сверхмалых процентов
        def format_pct(pct):
            if pct > 0 and pct < 0.01:
                return f"{pct:.5f}"
            return f"{pct:.2f}"

        if actor['tool'] == 'JMETER':
            ev['load_config'] = f"<b>JMETER</b>: {actor['load']} RPS"
            ev['rps_display'] = f"{actual_rps:,.1f}" + (f" / {actor['load']}" if actor['load'] and actor['load'] != '?' else "")
            
            if errors >= fatal_limit:
                ev['status_txt'], ev['status_cls'] = "FAIL", "status-fail"
                ev['err_style'], ev['err_display'] = "color:#e74c3c; font-weight:bold;", f"{errors} err"
            elif errors >= warn_limit:
                ev['status_txt'], ev['status_cls'] = "DEGRADED", "status-warning"
                ev['err_style'], ev['err_display'] = "color:#f39c12; font-weight:bold;", f"{errors} err"
            else:
                ev['status_txt'], ev['status_cls'] = "PASS", "status-pass"
                ev['err_style'], ev['err_display'] = "color:#27ae60; font-weight:bold;", f"{errors} err"

        elif actor['tool'] == 'TREX':
            mult = actor.get('mult', '?')
            # 🟢 ФИКС: Умная подстановка pps/cps
            unit = "pps" if ('UDP_PPS' in p_name or 'STL' in p_name) else "cps"
            ev['load_config'] = f"TREX: {mult}x1000 {unit}"
            
            tx_bw = stats.get('max_tx_bw', '0 bps')
            drops = stats.get('astf_drops', 0)
            drop_pct = stats.get('drop_pct', 0.0)
            malware_drops = stats.get('malware_drops', 0)
            malware_tx = stats.get('malware_tx', 0)
            
            ev['rps_display'] = f"Max TX: {tx_bw}"
            
            # Проброс задержки в интерфейс
            lat_ms = stats.get('latency_ms', 0.0)
            ev['response_time'] = f"{lat_ms:.2f} ms" if lat_ms > 0 else "N/A"

            if is_ips:
                malware_pct = (malware_drops / malware_tx * 100.0) if malware_tx > 0 else 0.0
                
                ev['err_display'] = (
                    f"Legit Drops: {drops} ({format_pct(drop_pct)}%)<br>"
                    f"<span style='color:#696969; font-size:0.8em; font-style:italic;'>"
                    f"Malware Blocked: {malware_drops} ({malware_pct:.1f}%)</span>"
                )

                # Строгая Data-Plane логика (игнорируем Control Plane, как в sc_logic.py)
                if drop_pct >= fatal_limit:
                    ev['status_txt'], ev['status_cls'] = "DoS", "status-fail"
                    ev['err_style'] = "color:#e74c3c; font-weight:bold;"
                elif drop_pct >= warn_limit:
                    ev['status_txt'], ev['status_cls'] = "DEGRADED", "status-warning"
                    ev['err_style'] = "color:#f39c12; font-weight:bold;"
                else:
                    if malware_drops > 0:
                        ev['status_txt'], ev['status_cls'] = "SECURED", "status-blocked"
                        ev['err_style'] = "color:#2980b9; font-weight:bold;"
                    else:
                        ev['status_txt'], ev['status_cls'] = "BYPASSED", "status-fail"
                        ev['err_style'] = "color:#e74c3c; font-weight:bold;"
            # 🟢 НОВАЯ ЛОГИКА: Интеллектуальный поиск точки излома емкости (MAX_CC)
            elif 'max_cc' in p_name.lower():
                ts_flows = stats.get('time_series', {}).get('active_flows', [])
                ts_drops = stats.get('time_series', {}).get('drops', [])
                
                real_max_cc = 0
                break_drops = 0
                limit_hit = False
                
                # Идем по оси времени (как на графике)
                for i in range(len(ts_flows)):
                    c_flows = ts_flows[i]
                    c_drops = ts_drops[i]
                    
                    # Считаем, сколько дропов нам разрешено иметь в эту секунду
                    # Берем минимум 10, чтобы микро-скачок не зарубил тест раньше времени
                    allowed_drops = max(10, c_flows * (fatal_limit / 100.0))
                    
                    if c_drops > allowed_drops:
                        limit_hit = True
                        break_drops = c_drops # Запоминаем, сколько дропов было в момент смерти
                        break # 🛑 Фаервол сдох! Останавливаем счетчик
                    
                    if c_flows > real_max_cc:
                        real_max_cc = c_flows
                
                # Если лимит не пробит, берем просто максимум дропов (для Steady State)
                if not limit_hit and ts_drops:
                    break_drops = max(ts_drops)

                # Форматируем красивую циферку
                cc_formatted = f"{real_max_cc/1e6:.2f}M" if real_max_cc >= 1e6 else f"{real_max_cc:,.0f}".replace(',', ' ')
                
                # Сохраняем это честное значение в eval, чтобы потом и в шапку его подставить
                ev['real_max_cc'] = real_max_cc
                
                if limit_hit or (real_max_cc == 0):
                    # Потолок достигнут и пробит
                    ev['status_txt'], ev['status_cls'] = "LIMIT FOUND", "status-warning" 
                    ev['err_style'] = "color:#8e44ad; font-weight:bold;" 
                    ev['err_display'] = f"Capped at ~{cc_formatted} CC<br><span style='font-size:0.8em; color:#e74c3c;'>Broke at {break_drops} drops</span>"
                    ev['hc_icon'] = '<span title="State Table Exhausted" style="color:#8e44ad; font-weight:bold; font-size:1.1em;">🚧 Exhausted</span>'
                else:
                    # Влили всё, фаервол не упал
                    ev['status_txt'], ev['status_cls'] = "NOT REACHED", "status-pass" 
                    ev['err_style'] = "color:#27ae60; font-weight:bold;"
                    ev['err_display'] = f"Peak: {cc_formatted} CC<br><span style='font-size:0.8em; color:#7f8c8d;'>Steady State: {break_drops} drops</span>"
                    ev['hc_icon'] = '<span title="Capacity Not Reached" style="color:#27ae60; font-weight:bold; font-size:1.1em;">✅ Stable</span>'
            else:
                ev['err_display'] = f"Drops: {drops} ({format_pct(drop_pct)}%)" 
                
                if drop_pct >= fatal_limit:
                    ev['status_txt'], ev['status_cls'] = "DoS", "status-fail"
                    ev['err_style'] = "color:#e74c3c; font-weight:bold;"
                elif drop_pct >= warn_limit:
                    ev['status_txt'], ev['status_cls'] = "DEGRADED", "status-warning"
                    ev['err_style'] = "color:#f39c12; font-weight:bold;"
                else:
                    ev['status_txt'], ev['status_cls'] = "PASS", "status-pass"
                    ev['err_style'] = "color:#27ae60; font-weight:bold;"
                
        return ev

    def render_html(self, data):
        Log.info(f"[{self.__class__.__name__}] Generating HTML with Dynamic Data-Driven Template...")
        
        # 🟢 АРХИТЕКТУРНЫЙ ФИКС: Поднимаем контекст (Hoisting) в начало области видимости
        # 1. Извлекаем сырые тайтлы (нужны для определения типа теста)
        base_title, base_subtitle = self._format_session_label(data.get('label', ''), data)
        
        # 2. Определяем Data-Driven стратегию рендера
        is_cc_test = '[SYN6' in str(base_title)
        is_stl_test = 'UDP_PPS' in str(base_title) or 'STL' in str(base_title)
        # 🟢 НОВОЕ: Детектируем Soak Test
        is_soak_test = '[ST' in str(base_title) or 'Stability' in str(base_title)
        # CPS тест не должен триггериться на STL/CC/Soak-тесты
        is_cps_test = ('[SYN' in str(base_title) or '[RS' in str(base_title)) and not is_cc_test and not is_stl_test and not is_soak_test
        
        # 🟢 Динамические лейблы для шапки
        if is_cc_test:
            primary_metric_label = "Max Concurrent Connections"
        elif is_cps_test:
            primary_metric_label = "Peak Connection Rate"
        elif is_stl_test:
            primary_metric_label = "Peak Forwarding Rate"
        elif is_soak_test:
            primary_metric_label = "Average Throughput"  # <-- Возвращаем гигабиты на базу!
        else:
            primary_metric_label = "Peak Throughput"

        overview_rows = ""
        artifacts_section_html = ""
        unified_chart_html = ""
        
        behavior = data.get('behavior', 'single')
        if len(data.get('iterations', [])) <= 1:
            behavior = 'single'

        peak_val = 0
        soak_total_duration = 0
        soak_total_drops = 0
        soak_avg_bps = 0
        soak_total_flows = 0
        session_has_dos = False 

        # 3. Вычисляем глобальный Peak для шапки
        for it in data.get('iterations', []):
            duration = int(it.get('duration', 60))
            soak_total_duration += duration
            
            for a in it.get('actors', []):
                if a['tool'] == 'TREX':
                    st = a.get('stats', {})
                    if is_cps_test:
                        try: val = float(a.get('mult', 0)) * 1000
                        except ValueError: val = 0
                    elif is_cc_test:
                        # 🟢 ФИКС: Берем вычисленный честный максимум до начала потерь
                        val = a.get('eval', {}).get('real_max_cc', 0)
                        if val == 0: # Фоллбэк на всякий случай
                            ts_flows = st.get('time_series', {}).get('active_flows', [])
                            val = max(ts_flows) if ts_flows else 0
                    elif is_stl_test:
                        tx_pkts = st.get('tx_pkts', 0)
                        val = (tx_pkts / duration) if duration > 0 else 0
                    else:
                        val = st.get('max_tx_bps_raw', 0)
                        
                    if val > peak_val: 
                        peak_val = val
                    
                    # Собираем данные для Soak Test
                    soak_total_drops += st.get('astf_drops', 0)
                    soak_avg_bps = st.get('max_tx_bps_raw', 0) # Для сингл-рана это и есть среднее
                    soak_total_flows += st.get('tx_pkts', 0)
                        
                status = a.get('eval', {}).get('status_txt', '')
                if status in ['DoS', 'FATAL', 'FAIL']:
                    session_has_dos = True

        # 🟢 Формируем значение для плашки Peak в шапке
        if is_cps_test:
            peak_str = f"{peak_val:,.0f} CPS".replace(',', ' ')
        elif is_cc_test:
            peak_str = f"{peak_val:,.0f} CC".replace(',', ' ')
        elif is_stl_test:
            peak_str = f"{peak_val/1e6:.2f} Mpps" if peak_val >= 1e6 else f"{peak_val/1e3:.2f} Kpps"
        else:
            # Для Soak Test и NDR тестов выводим честные Gbps
            peak_str = f"{peak_val/1e9:.2f} Gbps" if peak_val >= 1e9 else f"{peak_val/1e6:.2f} Mbps" if peak_val >= 1e6 else f"{peak_val/1e3:.2f} Kbps" if peak_val >= 1e3 else "0 bps"
        
        peak_color = "#333333"
        peak_html = f'<span style="color: {peak_color}; font-weight: 800;">{peak_str}</span>'

        # 🟢 РЕНДЕР: Certificate of Stability для Soak-тестов
        if is_soak_test:
            hours = soak_total_duration // 3600
            minutes = (soak_total_duration % 3600) // 60
            uptime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

            # ФИКС: Опираемся на session_has_dos (учитывает % fatal_limit), а не на абсолютный 0
            badge_color = "#27ae60" if not session_has_dos else "#e74c3c"
            badge_title = "🏆 Certificate of Stability" if badge_color == "#27ae60" else "💀 Stability Test Failed"
            throughput_str = f"{soak_avg_bps/1e9:.2f} Gbps" if soak_avg_bps >= 1e9 else f"{soak_avg_bps/1e6:.2f} Mbps"
            
            unified_chart_html = f"""
            <div class="iter-card" style="border-top: 4px solid {badge_color}; box-shadow: 0 4px 15px rgba(0,0,0,0.05);">
                <div class="iter-header" style="background: #f8f9fa; display:flex; justify-content:space-between; align-items:center;">
                    <span class="iter-title" style="color: {badge_color}; font-size: 18px; font-weight: bold;">{badge_title}</span>
                    <span style="font-family:monospace; color:#7f8c8d; font-size: 12px;">Endurance / Soak Test</span>
                </div>
                <div class="iter-body" style="text-align: center; padding: 30px 20px;">
                    <div style="display: flex; justify-content: space-around; flex-wrap: wrap; margin-bottom: 20px;">
                        <div>
                            <div style="font-size: 12px; color: #7f8c8d; text-transform: uppercase;">Continuous Uptime</div>
                            <div style="font-size: 32px; font-weight: 800; color: #2c3e50;">{uptime_str}</div>
                        </div>
                        <div>
                            <div style="font-size: 12px; color: #7f8c8d; text-transform: uppercase;">Average Load</div>
                            <div style="font-size: 32px; font-weight: 800; color: #2c3e50;">{throughput_str}</div>
                        </div>
                        <div>
                            <div style="font-size: 12px; color: #7f8c8d; text-transform: uppercase;">Total Drops</div>
                            <div style="font-size: 32px; font-weight: 800; color: {badge_color};">{soak_total_drops:,}</div>
                        </div>
                    </div>
                    <div style="font-size: 14px; color: #7f8c8d; background: #fff; display: inline-block; padding: 8px 16px; border-radius: 6px; border: 1px solid #ddd;">
                        Processed approx <b>{soak_total_flows:,.0f}</b> connections/packets without state table corruption.
                    </div>
                </div>
            </div>
            """

        # ФИКС: Отключаем рендер линейного графика для STL-тестов и Soak-тестов
        elif behavior == 'stepper' and not is_stl_test:
            trend_x_target, trend_y_main, trend_y_drops = [], [], []
            
            # DATA-DRIVEN: Динамические лейблы осей и графиков в зависимости от теста
            if is_cps_test:
                chart_title = "📈 График деградации (Connection Rate)"
                y1_name, y1_series = "CPS", "Achieved CPS"
            elif is_cc_test:
                chart_title = "📈 График деградации (Concurrent Connections)"
                y1_name, y1_series = "CC", "Active Conns"
            else:
                chart_title = "📈 График деградации пропускной способности (Knee Curve)"
                y1_name, y1_series = "Mbps", "Throughput (Mbps)"

            for it in data.get('iterations', []):
                duration = int(it.get('duration', 60)) 
                
                for a in it.get('actors', []):
                    if a['tool'] == 'TREX':
                        st = a.get('stats', {})
                        mult = float(a.get('mult', 0)) if str(a.get('mult', '')).replace('.', '').isdigit() else 0
                        
                        # 1. Ось X (Целевая нагрузка)
                        if is_cps_test or is_cc_test:
                            target_load = int(mult * 1000)
                            trend_x_target.append(f"{target_load} {y1_name}")
                        else:
                            target_load = mult
                            trend_x_target.append(f"Mult {target_load}x")
                        
                        # 2. Дропы
                        drops = st.get('astf_drops', 0)
                        trend_y_drops.append(drops)

                        # 3. ВЫСЧИТЫВАЕМ ДОСТИГНУТУЮ НАГРУЗКУ ДЛЯ ОСИ Y
                        if is_cps_test:
                            tx_conns = st.get('tx_pkts', 0)
                            achieved_val = round(max(0, tx_conns - drops) / duration) if duration > 0 else 0
                        elif is_cc_test:
                            achieved_val = max(0, target_load - drops)
                        else:
                            achieved_val = round(st.get('max_tx_bps_raw', 0) / 1e6, 2)
                            
                        trend_y_main.append(achieved_val)

            if trend_x_target:
                max_y_idx = trend_y_main.index(max(trend_y_main)) if trend_y_main else 0
                knee_x = trend_x_target[max_y_idx] if trend_y_main else ""
                
                if is_cps_test or is_cc_test:
                    peak_val_str = f"{max(trend_y_main):,.0f}".replace(',', ' ')
                else:
                    peak_val_str = f"{max(trend_y_main)}"
                
                unified_chart_html = """
                <div class="iter-card" style="border-top: 3px solid #3498db; box-shadow: 0 4px 10px rgba(52, 152, 219, 0.1);">
                    <div class="iter-header" style="background: #ebf5fb;"><span class="iter-title" style="color: #2980b9;">%(chart_title)s</span></div>
                    <div class="iter-body">
                        <div id="stepper-chart" style="width: 100%%; height: 350px;"></div>
                        <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
                        <script>
                            document.addEventListener("DOMContentLoaded", function() {
                                var chartElem = document.getElementById('stepper-chart');
                                if(chartElem) {
                                    echarts.init(chartElem).setOption({
                                        tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
                                        legend: { data: ['%(y1_series)s', 'Packet Drops'], bottom: 0 },
                                        grid: { top: 30, left: 60, right: 60, bottom: 40 },
                                        xAxis: { type: 'category', data: %(x_data)s, axisLine: { lineStyle: { color: '#bdc3c7' } } },
                                        yAxis: [
                                            { type: 'value', name: '%(y1_name)s', position: 'left', axisLabel: { color: '#2980b9' }, splitLine: { lineStyle: { type: 'dashed', color: '#ecf0f1' } } },
                                            { type: 'value', name: 'Drops', position: 'right', axisLabel: { color: '#e74c3c' }, splitLine: { show: false } }
                                        ],
                                        series: [
                                            { name: '%(y1_series)s', type: 'line', smooth: true, itemStyle: { color: '#2980b9' }, lineStyle: { width: 3 }, areaStyle: { opacity: 0.1 }, data: %(y_main)s,
                                              markLine: { silent: true, symbol: ['none', 'none'], label: { formatter: 'Knee Point\\n{c} %(y1_name)s', position: 'insideEndTop', color: '#e74c3c', padding: [4, 8], backgroundColor: 'rgba(255,255,255,0.85)', borderRadius: 4, borderWidth: 1, borderColor: '#e74c3c' }, lineStyle: { color: '#e74c3c', type: 'dashed', width: 2 }, data: [{ xAxis: '%(knee_x)s', name: '%(peak_val)s' }] }
                                            },
                                            { name: 'Packet Drops', type: 'line', yAxisIndex: 1, smooth: true, itemStyle: { color: '#e74c3c' }, lineStyle: { type: 'dashed', width: 2 }, data: %(y_drops)s }
                                        ]
                                    });
                                }
                            });
                        </script>
                    </div>
                </div>
                """ % { 
                    'chart_title': chart_title, 'y1_name': y1_name, 'y1_series': y1_series,
                    'x_data': json.dumps(trend_x_target), 'y_main': json.dumps(trend_y_main), 
                    'y_drops': json.dumps(trend_y_drops), 'knee_x': knee_x, 'peak_val': peak_val_str 
                }

        # Рендерим сертификат для Binary Search И для STL Stepper тестов!
        elif behavior == 'binary' or (behavior == 'stepper' and is_stl_test):
            max_pass_val, max_tx_pps, max_pass_bps = 0, 0, 0
            max_pass_mult = "N/A"
            
            for it in data.get('iterations', []):
                for a in it.get('actors', []):
                    if a['tool'] == 'TREX' and a.get('eval', {}).get('status_txt') in ['PASS', 'SECURED']:
                        
                        if is_stl_test:
                            tx_pkts = a.get('stats', {}).get('tx_pkts', 0)
                            val = (tx_pkts / int(it.get('duration', 60))) if int(it.get('duration', 60)) > 0 else 0
                        else:
                            val = float(a.get('mult', 0)) * 1000 if (is_cps_test or is_cc_test) else a.get('stats', {}).get('max_tx_bps_raw', 0)
                        
                        if val >= max_pass_val:
                            max_pass_val = val
                            max_pass_mult = str(a.get('mult', '?'))
                            max_tx_pps = val 
                            max_pass_bps = a.get('stats', {}).get('max_tx_bps_raw', 0)

            if is_stl_test:
                ndr_title = "Max Forwarding Rate (PPS)"
                ndr_primary = f"{max_tx_pps/1e6:.2f} Mpps" if max_tx_pps >= 1e6 else f"{max_tx_pps/1e3:.2f} Kpps"
                ndr_secondary = f"{max_pass_bps/1e9:.2f} Gbps" if max_pass_bps >= 1e9 else f"{max_pass_bps/1e6:.2f} Mbps"
            elif is_cps_test:
                ndr_title = "Max Stable Connection Rate"
                ndr_primary = f"{max_pass_val:,.0f} CPS".replace(',', ' ')
                ndr_secondary = f"{max_pass_bps/1e6:.2f} Mbps | {max_tx_pps/1e3:.2f} Kpps"
            elif is_cc_test:
                ndr_title = "Max Concurrent Connections"
                ndr_primary = f"{max_pass_val:,.0f} CC".replace(',', ' ')
                ndr_secondary = f"{max_pass_bps/1e6:.2f} Mbps | {max_tx_pps/1e3:.2f} Kpps"
            else:
                ndr_title = "Validated Non-Drop Rate (NDR)"
                ndr_primary = f"{max_pass_val/1e9:.2f} Gbps" if max_pass_val >= 1e9 else f"{max_pass_val/1e6:.2f} Mbps" if max_pass_val >= 1e6 else "0 bps"
                ndr_secondary = f"{max_tx_pps/1e6:.2f} Mpps" if max_tx_pps >= 1e6 else f"{max_tx_pps/1e3:.2f} Kpps" if max_tx_pps >= 1e3 else "0 pps"
            
            search_type = "Smart-Stepper Search" if behavior == 'stepper' else "RFC 2544 / Binary Search"
            
            unified_chart_html = f"""
            <div class="iter-card" style="border-top: 4px solid #27ae60; box-shadow: 0 4px 15px rgba(39, 174, 96, 0.1);">
                <div class="iter-header" style="background: #eafaf1; display:flex; justify-content:space-between; align-items:center;">
                    <span class="iter-title" style="color: #27ae60; font-size: 18px;">🏆 Certificate of Performance</span>
                    <span style="font-family:monospace; color:#7f8c8d; font-size: 12px;">{search_type}</span>
                </div>
                <div class="iter-body" style="text-align: center; padding: 40px 20px;">
                    <div style="font-size: 14px; color: #7f8c8d; text-transform: uppercase; letter-spacing: 2px; margin-bottom: 10px;">{ndr_title}</div>
                    <div style="font-size: 48px; font-weight: 800; color: #2c3e50; margin-bottom: 5px;">{ndr_primary}</div>
                    <div style="font-size: 18px; color: #7f8c8d; margin-bottom: 20px;">{ndr_secondary}</div>
                    <div style="font-size: 16px; color: #27ae60; font-family: monospace; background: #fff; display: inline-block; padding: 8px 16px; border-radius: 6px; border: 1px solid #27ae60;">
                        Profile Multiplier: {max_pass_mult}x
                    </div>
                </div>
            </div>
            """
        elif behavior == 'matrix':
            matrix_x_iters, matrix_y_bps, matrix_y_drops = [], [], []
            for it in data.get('iterations', []):
                trex_actor = next((a for a in it.get('actors', []) if a['tool'] == 'TREX'), None)
                if trex_actor:
                    matrix_x_iters.append(f"Config {it['id']}")
                    matrix_y_bps.append(round(trex_actor.get('stats', {}).get('max_tx_bps_raw', 0) / 1e6, 2))
                    matrix_y_drops.append(trex_actor.get('stats', {}).get('astf_drops', 0))

            if matrix_x_iters:
                unified_chart_html = """
                <div class="iter-card" style="border-top: 3px solid #8e44ad; box-shadow: 0 4px 10px rgba(142, 68, 173, 0.1);">
                    <div class="iter-header" style="background: #f4ecf8;"><span class="iter-title" style="color: #8e44ad;">📊 Матрица деградации DUT (Constant Load)</span></div>
                    <div class="iter-body">
                        <div id="matrix-chart" style="width: 100%%; height: 350px;"></div>
                        <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
                        <script>
                            document.addEventListener("DOMContentLoaded", function() {
                                var chartElem = document.getElementById('matrix-chart');
                                if(chartElem) {
                                    echarts.init(chartElem).setOption({
                                        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
                                        legend: { data: ['Throughput (Mbps)', 'Packet Drops'], bottom: 0 },
                                        grid: { top: 30, left: 60, right: 60, bottom: 40 },
                                        xAxis: { type: 'category', data: %(x_data)s, axisTick: { alignWithLabel: true }, axisLine: { lineStyle: { color: '#bdc3c7' } } },
                                        yAxis: [
                                            { type: 'value', name: 'Mbps', position: 'left', axisLabel: { color: '#8e44ad' }, splitLine: { lineStyle: { type: 'dashed', color: '#ecf0f1' } } },
                                            { type: 'value', name: 'Drops', position: 'right', axisLabel: { color: '#e74c3c' }, splitLine: { show: false } }
                                        ],
                                        series: [
                                            { name: 'Throughput (Mbps)', type: 'bar', barMaxWidth: 50, itemStyle: { color: '#8e44ad', borderRadius: [4, 4, 0, 0] }, data: %(y_bps)s },
                                            { name: 'Packet Drops', type: 'line', yAxisIndex: 1, smooth: true, symbolSize: 8, itemStyle: { color: '#e74c3c' }, lineStyle: { width: 3 }, data: %(y_drops)s }
                                        ]
                                    });
                                }
                            });
                        </script>
                    </div>
                </div>
                """ % { 'x_data': json.dumps(matrix_x_iters), 'y_bps': json.dumps(matrix_y_bps), 'y_drops': json.dumps(matrix_y_drops) }
        # =========================================================
        # 🟢 1. ПРЕДСКАНИРОВАНИЕ: Ищем Абсолютного Победителя (Max PASS)
        # =========================================================
        best_pass_idx = -1
        best_pass_actor_idx = -1
        max_load_found = -1

        for i, it in enumerate(data.get('iterations', [])):
            for j, a in enumerate(it.get('actors', [])):
                ev = a.get('eval', {})
                if ev.get('status_txt') in ['PASS', 'SECURED', 'LIMIT FOUND']:
                    try:
                        current_load = float(a.get('mult') or a.get('load') or 0)
                    except (ValueError, TypeError):
                        current_load = 0
                        
                    if current_load > max_load_found:
                        max_load_found = current_load
                        best_pass_idx = i
                        best_pass_actor_idx = j

        # =========================================================
        # 🟢 2. ОСНОВНОЙ ЦИКЛ СБОРКИ HTML-ТАБЛИЦЫ И АРТЕФАКТОВ
        # =========================================================
        # (Обнуляем на всякий случай перед сборкой)
        overview_rows = ""
        artifacts_section_html = ""

        for idx, it in enumerate(data.get('iterations', [])):
            iter_artifacts_inner = ""
            for a_idx, a in enumerate(it.get('actors', [])):
                ev = a.get('eval', {})
                st = a.get('stats', {})
                
                # =========================================================
                # 🟢 ВЫЧИСЛЯЕМ ФОН СТРОКИ НА ОСНОВЕ СТАТУСА ИТЕРАЦИИ
                # =========================================================
                status_txt = ev.get('status_txt', '').upper()
                
                if status_txt == 'LIMIT FOUND':
                    row_class = 'style="background-color: rgba(142, 68, 173, 0.05);"' # Нежно-фиолетовый
                elif idx == best_pass_idx and a_idx == best_pass_actor_idx:
                    row_class = 'class="row-pass"' # 🏆 ТОЛЬКО победитель!
                elif status_txt in ['FAIL', 'DOS', 'DEGRADED', 'BYPASSED']:
                    row_class = 'class="row-fail"' # 💀 Провалы
                else:
                    row_class = ev.get('row_style', '') # Фоллбэк
                
                rt_display = ev.get('response_time', '-')
                if a['tool'] == 'JMETER' and st.get('avg_rt') != '-':
                    rt_display = f"{st['avg_rt']} ms<br><span style='font-size:0.85em; color:#888;'>(Max: {st['max_rt']})</span>"

                # 🟢 Вставляем вычисленный row_class прямо в тег <tr>
                overview_rows += f"""
                <tr {row_class}>
                    <td>{ev.get('display_name', '')}</td>
                    <td>{a.get('start', it.get('start', ''))}</td>
                    <td>{it.get('duration', '?')}s</td>
                    <td>{ev.get('load_config', '')}</td>
                    <td>{ev.get('rps_display', '')}</td>
                    <td>{rt_display}</td> 
                    <td style="{ev.get('err_style', '')}">{ev.get('err_display', '')}</td>
                    <td><span class="{ev.get('status_cls', '')}">{ev.get('status_txt', '')}</span></td>
                    <td style="text-align:center; font-size:1.2em;">{ev.get('hc_icon', '-')}</td>
                </tr>
                """
                
                btns = "".join([f'<a href="{art["link"]}" class="btn {art.get("style", "btn")}" target="_blank">{art["name"]}</a> ' for art in a.get('artifacts', [])])
                if a['tool'] == 'TREX':
                    btns += f'<a href="stats_{a["log"].replace(".log", ".json")}" class="btn btn-console" target="_blank" style="background: #f39c12; color: #fff; border-color: #e67e22;">JSON Stats</a> '
                
                iter_artifacts_inner += f"""
                <div style="margin-bottom:10px; border-bottom:1px solid #eee; padding-bottom:10px;">
                    <div style="font-weight:bold; color:#555; margin-bottom:5px; font-size:13px;">
                        <span style="color:#2980b9;">{a['tool']}</span> - {a['log']}
                    </div>
                    <div style="display:flex; gap:10px;">{btns}</div>
                """

                if behavior == 'single' and a['tool'] == 'TREX' and not is_soak_test:
                    chart_id = f"chart-{it['id']}-{a['log'].replace('.log', '')}"
                    
                    if st.get('time_series') and len(st['time_series']['time_s']) > 0:
                        ts = st['time_series']
                        iter_artifacts_inner += f"""
                        <div style="margin-top: 15px; border: 1px solid #e0e0e0; background: #ffffff; padding: 15px; border-radius: 4px;">
                            <div style="display: flex; gap: 20px; font-family: monospace; margin-bottom: 5px; font-size: 13px; color: #2c3e50;">
                                <div>PEAK ACTIVE FLOWS: <strong style="color: #2980b9;">{max(ts['active_flows']):,.0f}</strong></div>
                                <div>TOTAL DROPS: <strong style="color: #e74c3c;">{st.get('astf_drops', 0):,.0f}</strong></div>
                            </div>
                            <div id="{chart_id}" style="width: 100%%; height: 350px;"></div>
                            <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
                            <script>
                                document.addEventListener("DOMContentLoaded", function() {{
                                    var chartElem = document.getElementById('{chart_id}');
                                    if(chartElem) {{
                                        echarts.init(chartElem).setOption({{
                                            title: {{ text: 'Capacity Degradation over Time', textStyle: {{ fontSize: 14, color: '#7f8c8d' }} }},
                                            tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
                                            legend: {{ data: ['Active Connections', 'Packet Drops'], bottom: 0 }},
                                            grid: {{ top: 40, bottom: 60, left: 60, right: 60 }},
                                            dataZoom: [ {{ type: 'inside' }}, {{ type: 'slider', bottom: 25, height: 20 }} ],
                                            xAxis: {{ type: 'category', data: {json.dumps(ts['time_s'])}, name: 'Time (s)', axisLine: {{ lineStyle: {{ color: '#bdc3c7' }} }} }},
                                            yAxis: [
                                                {{ type: 'value', name: 'Connections', position: 'left', axisLabel: {{ color: '#2980b9' }}, splitLine: {{ lineStyle: {{ type: 'dashed', color: '#ecf0f1' }} }} }},
                                                {{ type: 'value', name: 'Drops', position: 'right', axisLabel: {{ color: '#e74c3c' }}, splitLine: {{ show: false }} }}
                                            ],
                                            series: [
                                                {{ name: 'Active Connections', type: 'line', smooth: true, symbol: 'none', itemStyle: {{ color: '#2980b9' }}, lineStyle: {{ width: 3 }}, areaStyle: {{ opacity: 0.1 }}, data: {json.dumps(ts['active_flows'])} }},
                                                {{ name: 'Packet Drops', type: 'line', yAxisIndex: 1, smooth: true, symbol: 'none', itemStyle: {{ color: '#e74c3c' }}, lineStyle: {{ type: 'dashed', width: 2 }}, data: {json.dumps(ts['drops'])} }}
                                            ]
                                        }});
                                    }}
                                }});
                            </script>
                        </div>
                        """
                    elif st.get('chart_data') and st['chart_data'].get('x_usec'):
                        iter_artifacts_inner += f"""
                        <div style="margin-top: 15px; border: 1px solid #e0e0e0; background: #ffffff; padding: 15px; border-radius: 4px;">
                            <div style="display: flex; gap: 20px; font-family: monospace; margin-bottom: 5px; font-size: 13px; color: #2c3e50;">
                                <div>AVG LATENCY: <strong>{st.get('latency_avg', 0)} ms</strong></div>
                                <div>JITTER: <strong>{st.get('jitter', 0)} µs</strong></div>
                                <div>TOTAL DROPS: <strong style="color: #e74c3c;">{st.get('astf_drops', 0)}</strong></div>
                            </div>
                            <div id="{chart_id}" style="width: 100%%; height: 280px;"></div>
                            <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
                            <script>
                                document.addEventListener("DOMContentLoaded", function() {{
                                    var chartElem = document.getElementById('{chart_id}');
                                    if(chartElem) {{
                                        echarts.init(chartElem).setOption({{
                                            tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'shadow' }} }},
                                            grid: {{ top: 40, bottom: 60, left: 60, right: 30 }},
                                            dataZoom: [ {{ type: 'inside' }}, {{ type: 'slider', bottom: 10, height: 20 }} ],
                                            xAxis: {{ type: 'category', data: {json.dumps(st['chart_data']['x_usec'])}, name: 'Задержка', axisLabel: {{ color: '#7f8c8d', rotate: 45, formatter: function (v) {{ return (parseInt(v) / 1000).toFixed(1) + ' ms'; }} }}, axisLine: {{ lineStyle: {{ color: '#bdc3c7' }} }} }},
                                            yAxis: {{ type: 'log', name: 'Пакеты (Log)', min: 1, splitLine: {{ lineStyle: {{ type: 'dashed', color: '#ecf0f1' }} }}, axisLabel: {{ color: '#7f8c8d' }} }},
                                            series: [{{ data: {json.dumps(st['chart_data']['y_count'])}, type: 'bar', itemStyle: {{ color: '#34495e' }}, barMaxWidth: 30, markLine: {{ silent: true, lineStyle: {{ color: '#e74c3c', type: 'dashed', width: 2 }}, label: {{ formatter: 'SLA (5ms)', position: 'insideEndTop' }}, data: [{{ xAxis: '5000' }}] }} }}]
                                        }});
                                    }}
                                }});
                            </script>
                        </div>
                        """
                iter_artifacts_inner += "</div>"
            artifacts_section_html += f'<div class="iter-card"><div class="iter-header"><span class="iter-title">Iteration #{it["id"]} Artifacts</span></div><div class="iter-body">{iter_artifacts_inner}</div></div>'

        # =========================================================
        # 🚀 ИНТЕРАКТИВНЫЕ ГРАФИКИ (VictoriaMetrics + Zabbix)
        # =========================================================
        target_health_html = ""
        try:
            from reporting.chart_builder import ChartDataBuilder
            
            # --- КОНФИГ VICTORIA METRICS ---
            monitor_node = getattr(self, 'config', {}).get('nodes', {}).get('monitor', {})
            vm_ip = monitor_node.get('net', {}).get('ip', '10.207.129.23') 
            vm_port = monitor_node.get('services', {}).get('victoria_api', {}).get('port', 8428)
            vm_url = f"http://{vm_ip}:{vm_port}"

            # --- КОНФИГ ZABBIX (Пока хардкодим, потом вынесешь в YAML) ---
            zb_url = "http://10.207.87.13/"
            zb_token = "ba77f0cfabc7f48fc51d354dce3f763a281673a8293f57a9c39ab5a88bcc9c79"  # ВПИШИ СЮДА ТОКЕН!
            zb_tag = "ActiveDUT"

            # ИДЕАЛЬНОЕ вычисление таймфрейма
            session_date = self.session_id.split('_')[0] 
            start_ts = int(datetime.strptime(f"{session_date} {data['start']}", "%Y%m%d %H:%M:%S").timestamp())
            end_ts = int(datetime.strptime(f"{session_date} {data['end']}", "%Y%m%d %H:%M:%S").timestamp())
            start_ts -= 30
            end_ts += 30

            # Дергаем Билдер
            builder = ChartDataBuilder(vm_url=vm_url, zabbix_url=zb_url, zabbix_token=zb_token)
            tp_data = builder.build_throughput_chart(self.session_id, start_ts, end_ts)
            fs_data = builder.build_flow_state_chart(self.session_id, start_ts, end_ts)
            zb_data = builder.build_target_health_chart(zb_tag, "system.cpu.util", "system.ram.util", start_ts, end_ts)
            
            chart_html = []
            
            # 🟢 БЛОК 1: VICTORIA METRICS (Если есть данные)
            if tp_data["time"] or fs_data["time"]:
                chart_html.append("""
                <div class="iter-card" style="border-top: 4px solid #2980b9;">
                    <div class="iter-header" style="background: #ebf5fb;">
                        <span class="iter-title" style="color: #2980b9;">📊 Traffic & State Dynamics (VictoriaMetrics)</span>
                    </div>
                    <div class="iter-body" style="display: flex; gap: 20px; flex-wrap: wrap; padding: 20px;">
                """)
                
                if tp_data["time"]:
                    chart_html.append(f"""
                        <div style="flex: 1; min-width: 450px; background: #fff; padding: 15px; border: 1px solid #eee; border-radius: 8px;">
                            <h4 style="text-align: center; color: #34495e; margin: 0 0 10px 0; font-size: 14px;">L2 Throughput Profile</h4>
                            <div id="vm-throughput-chart" style="width: 100%; height: 320px;"></div>
                        </div>
                    """)
                
                if fs_data["time"]:
                    chart_html.append(f"""
                        <div style="flex: 1; min-width: 450px; background: #fff; padding: 15px; border: 1px solid #eee; border-radius: 8px;">
                            <h4 style="text-align: center; color: #34495e; margin: 0 0 10px 0; font-size: 14px;">TCP Flow State Machine</h4>
                            <div id="vm-flow-chart" style="width: 100%; height: 320px;"></div>
                        </div>
                    """)
                chart_html.append("</div></div>")

            # 🟢 БЛОК 2: ZABBIX HEALTH (Если есть данные)
            zb_series = []
            zb_legend = []
            if zb_data.get("time"):
                colors_cpu = ['#e74c3c', '#e67e22', '#f1c40f'] # Красные/Оранжевые для CPU
                colors_ram = ['#9b59b6', '#3498db', '#2ecc71'] # Фиолетовые/Синие для RAM
                
                for idx, (hostname, metrics) in enumerate(zb_data["hosts"].items()):
                    c_cpu = colors_cpu[idx % len(colors_cpu)]
                    c_ram = colors_ram[idx % len(colors_ram)]
                    
                    zb_legend.extend([f'{hostname} CPU', f'{hostname} RAM'])
                    
                    zb_series.append({
                        "name": f'{hostname} CPU', "type": "line", "showSymbol": False, 
                        "itemStyle": {"color": c_cpu}, "data": metrics["cpu"]
                    })
                    zb_series.append({
                        "name": f'{hostname} RAM', "type": "line", "yAxisIndex": 1, "showSymbol": False,
                        "lineStyle": {"type": "dashed"}, "itemStyle": {"color": c_ram}, "data": metrics["ram"]
                    })

                chart_html.append(f"""
                <div class="iter-card" style="border-top: 4px solid #8e44ad; margin-top: 30px;">
                    <div class="iter-header" style="background: #f4ecf7;">
                        <span class="iter-title" style="color: #8e44ad;">🏥 Infrastructure Health (Zabbix: {zb_tag})</span>
                    </div>
                    <div class="iter-body" style="padding: 20px;">
                        <div id="zb-health-chart" style="width: 100%; height: 320px;"></div>
                    </div>
                </div>
                """)

            # 🟢 РЕНДЕР JS ДЛЯ ВСЕХ ГРАФИКОВ
            if chart_html:
                chart_html.append("""
                    <script src="../static/echarts.min.js"></script>
                    <script>
                        document.addEventListener("DOMContentLoaded", function() {
                """)
                
                if tp_data.get("time"):
                    chart_html.append(f"""
                            var tpElem = document.getElementById('vm-throughput-chart');
                            if(tpElem) {{ echarts.init(tpElem).setOption({{
                                tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
                                legend: {{ data: ['TX (Gbps)', 'RX (Gbps)'], bottom: 5 }},
                                grid: {{ top: 40, left: 50, right: 20, bottom: 60 }},
                                xAxis: {{ type: 'category', data: {json.dumps(tp_data['time'])}, axisLabel: {{ formatter: function (v) {{ let d = new Date(parseInt(v)); return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0'); }} }} }},
                                yAxis: {{ type: 'value', name: 'Gbps', nameGap: 15 }},
                                series: [
                                    {{ name: 'TX (Gbps)', type: 'line', showSymbol: false, itemStyle: {{color: '#2ecc71'}}, areaStyle: {{opacity: 0.1}}, data: {json.dumps(tp_data['tx'])} }},
                                    {{ name: 'RX (Gbps)', type: 'line', showSymbol: false, itemStyle: {{color: '#27ae60'}}, data: {json.dumps(tp_data['rx'])} }}
                                ]
                            }}); }}
                    """)

                if fs_data.get("time"):
                    chart_html.append(f"""
                            var fsElem = document.getElementById('vm-flow-chart');
                            if(fsElem) {{ echarts.init(fsElem).setOption({{
                                tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
                                legend: {{ data: ['Active Flows', 'CPS'], bottom: 5 }},
                                grid: {{ top: 40, left: 75, right: 75, bottom: 60 }},
                                xAxis: {{ type: 'category', data: {json.dumps(fs_data['time'])}, axisLabel: {{ formatter: function (v) {{ let d = new Date(parseInt(v)); return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0'); }} }} }},
                                yAxis: [
                                    {{ type: 'value', name: 'Flows', nameGap: 15, position: 'left', alignTicks: true, axisLine: {{show: true, lineStyle: {{color: '#3498db'}}}} }},
                                    {{ type: 'value', name: 'CPS', nameGap: 15, position: 'right', alignTicks: true, splitLine: {{show: false}}, axisLine: {{show: true, lineStyle: {{color: '#e67e22'}}}} }}
                                ],
                                series: [
                                    {{ name: 'Active Flows', type: 'line', showSymbol: false, itemStyle: {{color: '#3498db'}}, data: {json.dumps(fs_data['active_flows'])} }},
                                    {{ name: 'CPS', type: 'line', yAxisIndex: 1, showSymbol: false, itemStyle: {{color: '#e67e22'}}, data: {json.dumps(fs_data['cps'])} }}
                                ]
                            }}); }}
                    """)
                    
                if zb_data.get("time"):
                    chart_html.append(f"""
                            var zbElem = document.getElementById('zb-health-chart');
                            if(zbElem) {{ echarts.init(zbElem).setOption({{
                                tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }} }},
                                legend: {{ data: {json.dumps(zb_legend)}, bottom: 5 }},
                                grid: {{ top: 40, left: 50, right: 50, bottom: 60 }},
                                xAxis: {{ type: 'category', data: {json.dumps(zb_data['time'])}, axisLabel: {{ formatter: function (v) {{ let d = new Date(parseInt(v)); return d.getHours().toString().padStart(2,'0') + ':' + d.getMinutes().toString().padStart(2,'0'); }} }} }},
                                yAxis: [
                                    {{ type: 'value', name: 'CPU %', nameGap: 15, position: 'left', max: 100, axisLine: {{show: true, lineStyle: {{color: '#e74c3c'}}}} }},
                                    {{ type: 'value', name: 'RAM %', nameGap: 15, position: 'right', max: 100, splitLine: {{show: false}}, axisLine: {{show: true, lineStyle: {{color: '#9b59b6'}}}} }}
                                ],
                                series: {json.dumps(zb_series)}
                            }}); }}
                    """)

                chart_html.append("});\n</script>")
                target_health_html = "".join(chart_html)
                
        except Exception as e:
            Log.error(f"[{self.__class__.__name__}] Не удалось сгенерировать графики: {e}")

        cleaned_log = self._read_and_clean_session_log(self.session_log_path)
        log_section_html = f'<div class="iter-card"><div class="iter-header"><span class="iter-title">Full Session Log</span></div><div class="iter-body" style="padding:0;"><pre class="log-view">{cleaned_log}</pre></div></div>'

        meta = self._load_session_meta()
        thresholds = meta.get('thresholds', {})
        if is_cc_test:
            warn_val = "N/A"
            fatal_val = "(Stress to Failure)"
        else:
            warn_val = str(float(thresholds.get('warn', 0.05)))
            fatal_val = str(float(thresholds.get('fatal', 0.1)))

        total_duration_str = "~"
        try: 
            total_duration_str = str(datetime.strptime(data['end'], "%H:%M:%S") - datetime.strptime(data['start'], "%H:%M:%S"))
        except Exception: 
            pass
        
        dut_label = meta.get('dut_label')
        description = meta.get('description')

        clean_title = META_TAG_PATTERN.sub('', str(base_title)).strip()
        clean_subtitle = META_TAG_PATTERN.sub('', str(base_subtitle)).strip()

        if dut_label and dut_label != "Unknown DUT":
            fancy_title = f"{dut_label} <span style='font-size:0.75em; color:#7f8c8d; font-weight:normal;'>| {clean_subtitle}</span>"
        else:
            fancy_title = clean_title

        if description:
            fancy_subtitle = f"{clean_title}<br><span style='font-size:0.95em; color:#7f8c8d; margin-top:5px; display:inline-block;'>{description}</span>"
        else:
            fancy_subtitle = clean_title

        return NGFW_SESSION_REPORT_TEMPLATE.format(
            session_id=self.session_id, 
            label=fancy_title, 
            subtitle=fancy_subtitle,
            start_time=data['start'], 
            total_duration=total_duration_str,
            run_count=len(data.get('iterations', [])), 
            total_tests=data.get('eval_meta', {}).get('total_tests', 0),
            peak_label=primary_metric_label,
            peak_bw=peak_html, 
            warn_limit=warn_val, 
            fatal_limit=fatal_val,
            
            # ВОТ ЭТИ 5 СТРОК ДОЛЖНЫ БЫТЬ ОБЯЗАТЕЛЬНО:
            certificate_section=unified_chart_html,   
            chart_section=target_health_html,         
            overview_rows=overview_rows,              
            artifacts_section=artifacts_section_html, 
            log_section=log_section_html,             
            
            gen_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )