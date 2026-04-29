#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import re
import os
from pmi_logger import Log
from reporting.strategies.ddos_strategy import DDoSReportStrategy

class NGFWReportStrategy(DDoSReportStrategy):
    """
    Стратегия для отчетов NGFW.
    Наследует базовый парсинг логов (итерации, JMeter), но:
    1. Ищет флаг Malware в pmi_session.log для каждого рана TRex.
    2. Парсит счетчик Drops из L7-логов ASTF.
    3. Применяет строгую бизнес-логику оценки (Drops = Fail для легитима, Drops = Pass для IPS).
    """

    def parse_logs(self):
        """Переопределяем: вызываем базовый парсер, затем добавляем специфику NGFW"""
        data = super().parse_logs()

        malware_logs = set()
        mult_map = {}
        hc_list = [] # Список статусов Health Check

        if os.path.exists(self.session_log_path):
            with open(self.session_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    # 1. Парсим параметры запуска TRex
                    if 'TRex command generated:' in line:
                        m_cmd = re.search(r'\.py\s+([\d\.]+)\s+\d+\s+([^\s{]+)', line)
                        if m_cmd:
                            mult_val = m_cmd.group(1)
                            log_base = m_cmd.group(2)
                            mult_map[log_base + '.log'] = mult_val
                        if '"inject_malware": 1' in line and m_cmd:
                            malware_logs.add(log_base + '.log')
                            
                    # 2. Ловим результаты Health Check из лога
                    elif 'Health Check Passed' in line:
                        hc_list.append('🏥✅')
                    elif 'Health Check Failed' in line or 'Health Check Error' in line:
                        hc_list.append('🏥❌')

        # 3. Раскидываем собранные флаги по акторам в итерациях
        for i, it in enumerate(data.get('iterations', [])):
            # Если HC не запускался или лог оборвался, оставляем пустоту
            hc_icon = hc_list[i] if i < len(hc_list) else ''
            
            for a in it['actors']:
                a['is_ips'] = (a['log'] in malware_logs)
                if a['log'] in mult_map:
                    a['mult'] = mult_map[a['log']]
                a['hc_icon'] = hc_icon # Пробрасываем значок в статусы

        return data

    def _calculate_status(self, actor, stats, actual_rps, errors, total):
        ev = {'status_txt': 'UNKNOWN', 'status_cls': 'status-fail', 'err_style': 'color:#ccc;'}
        p_name = actor.get('profile', 'unknown').upper()
        
        is_baseline = 'BASELINE' in p_name
        is_ips = actor.get('is_ips', False)

        if is_ips:
            ev['display_name'] = f"🛡️ <b>{p_name}</b>"
            ev['row_style'] = 'style="background-color: #fcf3cf;"' 
        elif is_baseline:
            ev['display_name'] = f"⭐ <b>{p_name}</b>"
            ev['row_style'] = 'style="border-bottom: 3px solid #7f8c8d; background-color: #f8f9fa;"'
        else:
            ev['display_name'] = f"<b>{p_name}</b>"
            ev['row_style'] = ""

        thresholds = self.config.get('program', {}).get('dut', {}).get('thresholds', {})
        warn_limit = thresholds.get('warn', 10)
        fatal_limit = thresholds.get('fatal', 50)
        
        ev['hc_icon'] = actor.get('hc_icon', '-')

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
            ev['load_config'] = f"<b>TREX</b>: {mult}x 1000 cps" # <-- Исправили название
            
            tx_bw = stats.get('max_tx_bw', '0 bps')
            drops = stats.get('astf_drops', 0)
            
            ev['rps_display'] = f"Max TX: {tx_bw}"
            ev['err_display'] = f"Drops: {drops}" # <-- Убрали дублирование порогов

            if is_ips:
                if drops > 0:
                    ev['status_txt'], ev['status_cls'] = "SECURED", "status-blocked"
                    ev['err_style'] = "color:#2980b9; font-weight:bold;"
                else:
                    ev['status_txt'], ev['status_cls'] = "BYPASSED", "status-fail"
                    ev['err_style'] = "color:#e74c3c; font-weight:bold;"
            else:
                if drops >= fatal_limit:
                    ev['status_txt'], ev['status_cls'] = "FAIL", "status-fail"
                    ev['err_style'] = "color:#e74c3c; font-weight:bold;"
                elif drops >= warn_limit:
                    ev['status_txt'], ev['status_cls'] = "WARN", "status-warning"
                    ev['err_style'] = "color:#f39c12; font-weight:bold;"
                else:
                    ev['status_txt'], ev['status_cls'] = "PASS", "status-pass"
                    ev['err_style'] = "color:#27ae60; font-weight:bold;"
                
        return ev

    def _format_session_label(self, raw_label, session_data):
        """
        Переопределенный парсер заголовков для формата NGFW.
        Ожидает: North-South Degradation Matrix [CAP3_PD1_NS] (Perimeter Mix) (Medium Load ~5,2 Gbps (Mult 30 x 1000 cps))
        """
        import re
        
        # 1. Ищем блок нагрузки с конца строки
        m_params = re.search(r'\((Low|Medium|High).*?\)$', raw_label, re.IGNORECASE)
        
        if m_params:
            load_block = m_params.group(0) 
            title_part = raw_label[:m_params.start()].strip()
            final_subtitle = load_block[1:-1] # Убираем крайние скобки
        else:
            title_part = raw_label
            final_subtitle = ""

        # 2. Извлекаем тег [CAP3_PD1_NS]
        m_tag = re.search(r'\[(.*?)\]', title_part)
        tag = f"[{m_tag.group(1)}]" if m_tag else ""
        title_part = title_part.replace(m_tag.group(0), '') if m_tag else title_part

        # 3. Извлекаем (Perimeter Mix)
        m_desc = re.search(r'\((.*?)\)', title_part)
        desc = f"({m_desc.group(1)})" if m_desc else ""
        title_part = title_part.replace(m_desc.group(0), '') if m_desc else title_part

        # 4. Чистим мусор
        clean_title = re.sub(r'^\+\s*|\s*\+$', '', re.sub(r'\s+', ' ', title_part).strip()).strip()
        final_label = f"{tag} {desc}".strip() or clean_title

        # 5. Склеиваем подзаголовок
        if final_subtitle:
            # Для гибридных тестов подставляем RPS из JMeter
            if "RPS" not in final_subtitle and session_data:
                try:
                    jmeter_load = next((a['load'] for it in session_data.get('iterations', []) for a in it['actors'] if a['tool'] == 'JMETER' and a.get('load') and a['load'] != '?'), None)
                    if jmeter_load:
                        if re.match(r'^\d+m:', final_subtitle):
                            final_subtitle = re.sub(r'^(\d+m:)\s*', fr'\1 {jmeter_load} RPS Base, ', final_subtitle)
                        else:
                            final_subtitle = f"{jmeter_load} RPS Base, {final_subtitle}"
                except Exception:
                    pass

            if final_label != clean_title:
                final_subtitle = f"{clean_title} | {final_subtitle}"
        else:
            final_subtitle = "" if final_label == clean_title else clean_title

        return final_label, final_subtitle

    def _get_actor_stats_from_log(self, tool, log_path):
        stats = super()._get_actor_stats_from_log(tool, log_path)

        if tool == 'TREX' and os.path.exists(log_path):
            stats['astf_drops'] = 0
            stats['max_tx_bw'] = "0 bps"
            max_raw_bps = 0.0 # <-- ПЕРЕМЕННАЯ ДЛЯ ЧЕСТНОГО СРАВНЕНИЯ

            # Конвертер единиц измерения
            unit_mult = {'Gbps': 1e9, 'Mbps': 1e6, 'Kbps': 1e3, 'bps': 1}

            try:
                with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        if 'ASTF' in line:
                            if 'Drops:' in line:
                                m_drops = re.search(r'Drops:\s+(\d+)', line)
                                if m_drops and int(m_drops.group(1)) > stats['astf_drops']:
                                    stats['astf_drops'] = int(m_drops.group(1))
                            
                            # Ловим максимальную полосу
                            m_tx = re.search(r'TX:\s+([\d\.]+)([KMG]?bps)', line)
                            if m_tx:
                                val = float(m_tx.group(1))
                                unit = m_tx.group(2)
                                current_bps = val * unit_mult.get(unit, 1)
                                
                                # Сохраняем только если текущее значение больше максимального
                                if current_bps > max_raw_bps:
                                    max_raw_bps = current_bps
                                    stats['max_tx_bw'] = f"{val} {unit}"
            except Exception as e:
                Log.error(f"[NGFW Strategy] Error parsing ASTF log: {e}")
                
        return stats
    def render_html(self, data):
        """Полностью переопределенный рендер отчета для NGFW"""
        import os
        import shutil
        from datetime import datetime
        from reporting.html_templates import NGFW_SESSION_REPORT_TEMPLATE
        
        Log.info(f"[{self.__class__.__name__}] Generating HTML with custom NGFW template...")
        
        overview_rows = ""
        artifacts_section_html = ""
        
        for it in data['iterations']:
            iter_artifacts_inner = ""
            for a in it['actors']:
                ev = a.get('eval', {})
                st = a.get('stats', {})
                
                rt_display = "-"
                if a['tool'] == 'JMETER' and st.get('avg_rt') != '-':
                    rt_display = f"{st['avg_rt']} ms<br><span style='font-size:0.85em; color:#888;'>(Max: {st['max_rt']})</span>"
                elif a['tool'] == 'TREX': 
                    rt_display = "<span style='color:#555;'>N/A</span>"

                # Генерируем строку таблицы (ДОБАВЛЕНА КОЛОНКА HEALTH)
                overview_rows += f"""
                <tr {ev.get('row_style', '')}>
                    <td>{ev.get('display_name', '')}</td>
                    <td>{a.get('start', it['start'])}</td>
                    <td>{it.get('duration', '?')}s</td>
                    <td>{ev.get('load_config', '')}</td>
                    <td>{ev.get('rps_display', '')}</td>
                    <td>{rt_display}</td> 
                    <td style="{ev.get('err_style', '')}">{ev.get('err_display', '')}</td>
                    <td><span class="{ev.get('status_cls', '')}">{ev.get('status_txt', '')}</span></td>
                    <td style="text-align:center; font-size:1.2em;">{ev.get('hc_icon', '-')}</td>
                </tr>
                """
                
                # Кнопки артефактов
                btns = "".join([f'<a href="{art["link"]}" class="btn {art.get("style", "btn")}" target="_blank">{art["name"]}</a> ' for art in a.get('artifacts', [])])
                iter_artifacts_inner += f"""
                <div style="margin-bottom:10px; border-bottom:1px solid #eee; padding-bottom:10px;">
                    <div style="font-weight:bold; color:#555; margin-bottom:5px; font-size:13px;">
                        <span style="color:#2980b9;">{a['tool']}</span> - {a['log']}
                    </div>
                    <div style="display:flex; gap:10px;">{btns}</div>
                </div>
                """
                
            artifacts_section_html += f'<div class="iter-card"><div class="iter-header"><span class="iter-title">Iteration #{it["id"]} Artifacts</span></div><div class="iter-body">{iter_artifacts_inner}</div></div>'

        # Target Metrics Chart
        target_health_html = ""
        src_csv = os.path.join(self.logs_root, self.session_id, "target_metrics.csv")
        if os.path.exists(src_csv):
            shutil.copy2(src_csv, os.path.join(self.out_dir, "target_metrics.csv"))
            try:
                from reporting.chart_builder import build_target_chart_html
                target_health_html = build_target_chart_html(os.path.join(self.out_dir, "target_metrics.csv"))
            except ImportError: pass

        # Очистка сессионного лога
        cleaned_log = self._read_and_clean_session_log(self.session_log_path)
        log_section_html = f'<div class="iter-card"><div class="iter-header"><span class="iter-title">Full Session Log</span></div><div class="iter-body" style="padding:0;"><pre class="log-view">{cleaned_log}</pre></div></div>'

        # Итоговые цифры
        meta = data['eval_meta']
        # --- ВЫЧИСЛЕНИЕ ПИКОВОЙ ПРОПУСКНОЙ СПОСОБНОСТИ ---
        peak_bps = 0.0
        unit_mult = {'Gbps': 1e9, 'Mbps': 1e6, 'Kbps': 1e3, 'bps': 1}
        
        for it in data.get('iterations', []):
            for a in it['actors']:
                if a['tool'] == 'TREX':
                    bw_str = a.get('stats', {}).get('max_tx_bw', '0 bps')
                    m_bw = re.match(r'([\d\.]+)\s+([KMG]?bps)', bw_str)
                    if m_bw:
                        val = float(m_bw.group(1)) * unit_mult.get(m_bw.group(2), 1)
                        if val > peak_bps:
                            peak_bps = val
                            
        if peak_bps >= 1e9: peak_str = f"{peak_bps/1e9:.1f} Gbps"
        elif peak_bps >= 1e6: peak_str = f"{peak_bps/1e6:.1f} Mbps"
        elif peak_bps >= 1e3: peak_str = f"{peak_bps/1e3:.1f} Kbps"
        else: peak_str = "N/A"

        # Достаем пороги для шапки
        thresholds = self.config.get('program', {}).get('dut', {}).get('thresholds', {})
        warn_limit = thresholds.get('warn', 10)
        fatal_limit = thresholds.get('fatal', 50)

        total_duration_str = "~"
        try: total_duration_str = str(datetime.strptime(data['end'], "%H:%M:%S") - datetime.strptime(data['start'], "%H:%M:%S"))
        except: pass

        fancy_title, fancy_subtitle = self._format_session_label(data['label'], data)

        return NGFW_SESSION_REPORT_TEMPLATE.format(
            session_id=self.session_id, label=fancy_title, subtitle=fancy_subtitle,
            start_time=data['start'], total_duration=total_duration_str,
            run_count=len(data['iterations']), total_tests=data['eval_meta']['total_tests'],
            peak_bw=peak_str, warn_limit=warn_limit, fatal_limit=fatal_limit, # <-- Передаем новые переменные
            overview_rows=overview_rows,
            artifacts_section=artifacts_section_html, target_health_section=target_health_html,
            log_section=log_section_html, gen_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )