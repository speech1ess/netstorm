#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
from datetime import datetime
from pmi_logger import Log
from reporting.html_templates import SESSION_REPORT_TEMPLATE
from reporting.strategies.base_strategy import BaseReportStrategy

class DDoSReportStrategy(BaseReportStrategy):
    """
    Классическая стратегия для отчетов тестирования защиты от DDoS.
    Особенности логики: Дропы (ошибки) JMeter = FAIL, Дропы TRex = SUCCESS (Blocked).
    Наследует I/O и parse_logs от BaseReportStrategy.
    """

    def evaluate_metrics(self, data):
        """Этап 2: Копирование файлов, чтение статы и БИЗНЕС-ЛОГИКА (Вердикты)"""
        Log.info(f"[{self.__class__.__name__}] Evaluating metrics and copying artifacts...")
        data['eval_meta'] = {'total_rps_accum': 0, 'valid_rps_count': 0, 'total_tests': 0}

        for it in data['iterations']:
            for a in it['actors']:
                data['eval_meta']['total_tests'] += 1
                
                # 1. Копируем логи и JTL в директорию результатов
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

                # 2. Читаем статистику (Используем старый текстовый парсер)
                stats = self._get_actor_stats_from_log(a['tool'], dst_log)
                actual_rps = stats.get('rps', 0)
                errors = stats.get('errors', 0)
                total = stats.get('total', 0)
                
                if actual_rps > 0 and a['tool'] != 'TREX':
                    data['eval_meta']['total_rps_accum'] += actual_rps
                    data['eval_meta']['valid_rps_count'] += 1

                # 3. БИЗНЕС-ЛОГИКА ОЦЕНКИ
                eval_data = self._calculate_status(a, stats, actual_rps, errors, total)
                a['eval'] = eval_data  
                a['stats'] = stats

        return data

    def render_html(self, data):
        """Этап 3: Сборка HTML из готовых данных (без ECharts)"""
        Log.info(f"[{self.__class__.__name__}] Generating HTML...")
        overview_rows = ""
        artifacts_section_html = ""
        
        for it in data['iterations']:
            iter_artifacts_inner = ""
            for a in it['actors']:
                ev = a['eval']
                st = a['stats']
                
                rt_display = "-"
                if a['tool'] == 'JMETER' and st.get('avg_rt') != '-':
                    rt_display = f"{st['avg_rt']} ms<br><span style='font-size:0.85em; color:#888;'>(Max: {st['max_rt']})</span>"
                elif a['tool'] == 'TREX': 
                    rt_display = "<span style='color:#555;'>N/A</span>"

                overview_rows += f"""
                <tr {ev['row_style']}>
                    <td>{ev['display_name']}</td>
                    <td>{a.get('start', it['start'])}</td>
                    <td>{it.get('duration', '?')}s</td>
                    <td>{ev['load_config']}</td>
                    <td>{ev['rps_display']}</td>
                    <td>{rt_display}</td> 
                    <td style="{ev['err_style']}">{ev['err_display']}</td>
                    <td><span class="{ev['status_cls']}">{ev['status_txt']}</span></td>
                </tr>
                """
                
                btns = "".join([f'<a href="{art["link"]}" class="btn {art.get("style", "btn")}" target="_blank">{art["name"]}</a> ' for art in a['artifacts']])
                iter_artifacts_inner += f"""
                <div style="margin-bottom:10px; border-bottom:1px solid #eee; padding-bottom:10px;">
                    <div style="font-weight:bold; color:#555; margin-bottom:5px; font-size:13px;">
                        <span style="color:#2980b9;">{a['tool']}</span> - {a['log']}
                    </div>
                    <div style="display:flex; gap:10px;">{btns}</div>
                </div>
                """
                
            artifacts_section_html += f'<div class="iter-card"><div class="iter-header"><span class="iter-title">Iteration #{it["id"]} Artifacts</span></div><div class="iter-body">{iter_artifacts_inner}</div></div>'

        target_health_html = ""
        src_csv = os.path.join(self.logs_root, self.session_id, "target_metrics.csv")
        if os.path.exists(src_csv):
            shutil.copy2(src_csv, os.path.join(self.out_dir, "target_metrics.csv"))
            try:
                from reporting.chart_builder import build_target_chart_html
                target_health_html = build_target_chart_html(os.path.join(self.out_dir, "target_metrics.csv"))
            except ImportError: pass

        cleaned_log = self._read_and_clean_session_log(self.session_log_path)
        log_section_html = f'<div class="iter-card"><div class="iter-header"><span class="iter-title">Full Session Log</span></div><div class="iter-body" style="padding:0;"><pre class="log-view">{cleaned_log}</pre></div></div>'

        meta = data['eval_meta']
        avg_session_rps = round(meta['total_rps_accum'] / meta['valid_rps_count'], 1) if meta['valid_rps_count'] > 0 else 0
        total_duration_str = "~"
        try: total_duration_str = str(datetime.strptime(data['end'], "%H:%M:%S") - datetime.strptime(data['start'], "%H:%M:%S"))
        except: pass

        fancy_title, fancy_subtitle = self._format_session_label(data['label'], data)

        return SESSION_REPORT_TEMPLATE.format(
            session_id=self.session_id, label=fancy_title, subtitle=fancy_subtitle,
            start_time=data['start'], total_duration=total_duration_str,
            run_count=len(data['iterations']), total_tests=meta['total_tests'],
            avg_rps=avg_session_rps, overview_rows=overview_rows,
            artifacts_section=artifacts_section_html, target_health_section=target_health_html,
            log_section=log_section_html, gen_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

    # =========================================================================
    # INTERNAL HELPERS (Перенесены из старого скрипта без изменений логики)
    # =========================================================================

    def _calculate_status(self, actor, stats, actual_rps, errors, total):
        """Инкапсуляция правил оценки Anti-DDoS"""
        ev = {'status_txt': 'UNKNOWN', 'status_cls': 'status-fail', 'err_style': 'color:#ccc;'}
        p_name = actor.get('profile', 'unknown').upper()
        is_baseline = 'BASELINE' in p_name
        
        ev['display_name'] = f"⭐ <b>{p_name}</b>" if is_baseline else f"<b>{p_name}</b>"
        ev['row_style'] = 'style="border-bottom: 3px solid #7f8c8d; background-color: #f8f9fa;"' if is_baseline else ""

        if actor['tool'] == 'JMETER':
            ev['load_config'] = f"<b>JMETER</b>: {actor['load']} RPS"
            ev['rps_display'] = f"{actual_rps:,.1f}" + (f" / {actor['load']}" if actor['load'] and actor['load'] != '?' else "")
            error_rate = (errors / total * 100) if total > 0 else (100.0 if errors > 0 else 0.0)

            if is_baseline:
                if error_rate > 0.1:
                    ev['status_txt'], ev['status_cls'], ev['err_style'] = "FAIL", "status-fail", "color:#e74c3c; font-weight:bold;"
                    ev['err_display'] = f"{errors} ({error_rate:.2f}%) > 0.1%"
                else:
                    ev['status_txt'], ev['status_cls'], ev['err_display'] = "PASS", "status-pass", f"{errors} ({error_rate:.2f}%) < 0.1%"
            else:
                if error_rate >= 98.0:
                    ev['status_txt'], ev['status_cls'], ev['err_style'] = "BLOCKED", "status-blocked", "color:#2980b9; font-weight:bold;"
                    ev['err_display'] = f"{errors} ({error_rate:.2f}%) >= 98%"
                elif error_rate > 10.0:
                    ev['status_txt'], ev['status_cls'], ev['err_style'] = "PARTIAL", "status-warning", "color:#f39c12; font-weight:bold;"
                    ev['err_display'] = f"{errors} ({error_rate:.2f}%) > 10%"
                else:
                    ev['status_txt'], ev['status_cls'], ev['err_style'] = "BYPASSED", "status-fail", "color:#e74c3c; font-weight:bold;"
                    ev['err_display'] = f"{errors} ({error_rate:.2f}%) < 10%"

        elif actor['tool'] == 'TREX':
            raw_pps = float(actor.get('mult', 0)) * 1000 if str(actor.get('mult', '')).replace('.','').isdigit() else 0
            if raw_pps >= 1_000_000: ev['load_config'] = f"<b>TREX</b>: {raw_pps / 1_000_000:g} Mpps"
            elif raw_pps >= 1000: ev['load_config'] = f"<b>TREX</b>: {raw_pps / 1000:g} Kpps"
            else: ev['load_config'] = f"<b>TREX</b>: {actor.get('mult', '?')}x1000 pps"

            rx_pps = min(stats.get('rx_pps', 0.0), actual_rps)
            avg_bps = stats.get('avg_bps', 0.0)
            
            if avg_bps >= 1_000_000_000: bw_display = f"{avg_bps / 1_000_000_000:.1f} Gbps"
            elif avg_bps >= 1_000_000: bw_display = f"{avg_bps / 1_000_000:.1f} Mbps"
            elif avg_bps >= 1_000: bw_display = f"{avg_bps / 1_000:.1f} Kbps"
            else: bw_display = f"{avg_bps:.0f} bps"

            ev['rps_display'] = f"TX: {actual_rps:,.0f} pps<br>RX: {rx_pps:,.0f} pps<br><span style='font-size:0.85em; color:#888;'>(Avg BW: {bw_display})</span>"

            if actual_rps > 0:
                leakage_rate = min((rx_pps / actual_rps) * 100.0, 100.0)
                ev['err_display'] = f"Leakage: {leakage_rate:.2f}% < 2%"
                if leakage_rate > 2.0:
                    ev['status_txt'], ev['status_cls'], ev['err_style'] = "LEAK", "status-fail", "color:#e74c3c; font-weight:bold;"
                else:
                    ev['status_txt'], ev['status_cls'], ev['err_style'] = "BLOCKED", "status-blocked", "color:#2980b9; font-weight:bold;"
            else:
                ev['status_txt'], ev['status_cls'], ev['err_display'], ev['err_style'] = "NO TX", "status-fail", "0 TX", "color:#e74c3c; font-weight:bold;"
                
        return ev

    def _get_actor_stats_from_log(self, tool, log_path):
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
                            m = re.search(r'=\s+(?P<rate>[\d\.]+)/s.*Avg:\s+(?P<avg>\d+).*Max:\s+(?P<max>\d+).*Err:\s+(?P<err>\d+)', raw)
                            if m:
                                stats.update({'rps': float(m.group('rate')), 'avg_rt': m.group('avg'), 'max_rt': m.group('max'), 'errors': int(m.group('err'))})
                                try: stats['total'] = int(parts[1].strip().split()[0])
                                except: pass
                    elif tool == 'TREX':
                        if 'Total-Tx' in line: stats['raw_summary'] = line
                        elif 'TX:' in line and 'RX:' in line:
                            stats['raw_summary'] = line
                            m_time = re.search(r'\[\s*(?P<sec>\d+)s\]', line)
                            curr_sec = int(m_time.group('sec')) if m_time else None

                            val_tx, val_bps, val_rx = 0.0, 0.0, 0.0
                            m_tx = re.search(r'TX:\s+(?P<pps>[\d\.]+)(?P<mult>[kM]?)\s*pps\s*\((?P<bps>[\d\.]+)(?P<bmult>[kKMG]?)bps\)', line)
                            if m_tx:
                                val_tx = float(m_tx.group('pps')) * (1000000 if m_tx.group('mult') == 'M' else 1000 if m_tx.group('mult') == 'k' else 1)
                                bmult = m_tx.group('bmult').upper()
                                val_bps = float(m_tx.group('bps')) * (10**9 if bmult == 'G' else 10**6 if bmult == 'M' else 10**3 if bmult == 'K' else 1)
                            
                            m_rx = re.search(r'RX:\s+(?P<pps>[\d\.]+)(?P<mult>[kM]?)\s*pps', line)
                            if m_rx: val_rx = float(m_rx.group('pps')) * (1000000 if m_rx.group('mult') == 'M' else 1000 if m_rx.group('mult') == 'k' else 1)

                            if curr_sec is not None:
                                if 'last_sec' not in stats: stats.update({'last_sec': 0, 'total_tx_pkts': 0.0, 'total_rx_pkts': 0.0, 'total_tx_bits': 0.0})
                                delta_t = curr_sec - stats['last_sec']
                                if delta_t > 0:
                                    stats['total_tx_pkts'] += val_tx * delta_t
                                    stats['total_rx_pkts'] += val_rx * delta_t
                                    stats['total_tx_bits'] += val_bps * delta_t
                                    stats['last_sec'] = curr_sec
                                if stats['last_sec'] > 0:
                                    stats.update({'rps': stats['total_tx_pkts']/stats['last_sec'], 'rx_pps': stats['total_rx_pkts']/stats['last_sec'], 'avg_bps': stats['total_tx_bits']/stats['last_sec']})
                            else: stats.update({'rps': val_tx, 'rx_pps': val_rx, 'avg_bps': val_bps})

                        elif 'ASTF Active Flows' in line:
                            stats['raw_summary'] = line
                            m_time = re.search(r'\[\s*(?P<sec>\d+)s\]', line)
                            curr_sec = int(m_time.group('sec')) if m_time else None
                            val_bps = 0.0
                            m_tx = re.search(r'TX:\s+(?P<bps>[\d\.]+)(?P<bmult>[kMGT]?)bps', line)
                            if m_tx:
                                bmult = m_tx.group('bmult').upper()
                                val_bps = float(m_tx.group('bps')) * (10**9 if bmult == 'G' else 10**6 if bmult == 'M' else 10**3 if bmult == 'K' else 1)
                            
                            if curr_sec is not None:
                                if 'last_sec' not in stats: stats.update({'last_sec': 0, 'total_tx_bits': 0.0})
                                delta_t = curr_sec - stats['last_sec']
                                if delta_t > 0:
                                    stats['total_tx_bits'] += val_bps * delta_t
                                    stats['last_sec'] = curr_sec
                                if stats['last_sec'] > 0:
                                    stats['avg_bps'] = stats['total_tx_bits'] / stats['last_sec']
                                    stats['rps'] = stats['rx_pps'] = stats['avg_bps'] / 1000 
                            else: stats.update({'avg_bps': val_bps, 'rps': val_bps / 1000})
        except: pass
        return stats