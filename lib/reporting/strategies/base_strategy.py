# -*- coding: utf-8 -*-
import os
import re
import abc
import shutil
from datetime import datetime
from shared import SharedConfig
from pmi_logger import Log

# ─────────────────────────────────────────────────────────────
# REGEX PATTERNS (Универсальные маркеры сессии)
# ─────────────────────────────────────────────────────────────
RE_SESSION_START = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+=== ORCHESTRATOR START:\s+(?P<label>.*?)\s+===')
RE_ITERATION_START = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+Execution started\. Duration: (?P<dur>[\d\.]+)s')
RE_ACTOR_SPAWN = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+Spawn:\s+(?P<tool>\w+)\s+->\s+(?P<log>\S+)(?:.*\(Tput/Mult:\s+(?P<load>[\d\.\?]+)/(?P<mult>[\d\.\?]+)\))?')
RE_ITERATION_END = re.compile(r'(?:\[?(?P<time>\d{2}:\d{2}:\d{2})\]?\s+)?INFO\s+(Iteration execution finished|Orchestrator finished)\.')
RE_MANUAL_TREX = re.compile(r'INFO\s+TREX START: (?P<profile>.+) \(ID: (?P<id>[^\)]+)\)')
RE_MANUAL_JMETER = re.compile(r'INFO\s+JMETER START: (?P<profile>.+) \(ID: (?P<id>[^\)]+)\)')

class BaseReportStrategy(abc.ABC):
    """
    Абстрактный базовый класс конвейера отчетов.
    Инкапсулирует работу с файловой системой и парсинг таймлайна сессии.
    """
    def __init__(self, session_id, config=None):
        self.session_id = session_id
        self.config = config or {}
        self.logs_root = SharedConfig.get('paths.logs', '/opt/pmi/logs')
        self.results_root = SharedConfig.get('paths.results', '/opt/pmi/results')
        
        self.session_log_path = os.path.join(self.logs_root, session_id, "pmi_session.log")
        self.out_dir = os.path.join(self.results_root, session_id)
        
        self.parsed_data = {}
        self.evaluated_data = {}
        self.html_output = ""

    def run_pipeline(self):
        """Шаблонный метод (Template Method) конвейера сборки отчета"""
        os.makedirs(self.out_dir, exist_ok=True)
        
        Log.info(f"[{self.__class__.__name__}] 1. Parsing raw logs (I/O Layer)...")
        self.parsed_data = self.parse_logs()
        
        Log.info(f"[{self.__class__.__name__}] 2. Evaluating business logic metrics...")
        self.evaluated_data = self.evaluate_metrics(self.parsed_data)
        
        Log.info(f"[{self.__class__.__name__}] 3. Generating HTML (View Layer)...")
        self.html_output = self.render_html(self.evaluated_data)
        
        Log.info(f"[{self.__class__.__name__}] 4. Saving artifacts...")
        self.save_report()

    def parse_logs(self):
        """
        Универсальный парсер pmi_session.log. 
        Формирует Data Transfer Object (словарь итераций и акторов).
        Не содержит бизнес-логики оценок.
        """
        session = {
            'label': 'Manual / Single Run',
            'type': 'single',
            'start': '?',
            'end': '?',
            'iterations': []
        }
        
        if not os.path.exists(self.session_log_path):
            Log.error(f"[{self.__class__.__name__}] Session log not found: {self.session_log_path}")
            return session

        current_iter = None
        is_orchestrator = False

        def ensure_iter(start_time, dur=0):
            nonlocal current_iter
            if current_iter is None:
                current_iter = {'id': len(session['iterations']) + 1, 'start': start_time, 'duration': dur, 'actors': []}
                session['iterations'].append(current_iter)
            return current_iter

        # Прагматичный I/O: читаем файл построчно, не загружая весь дамп в RAM
        with open(self.session_log_path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                
                if m := RE_SESSION_START.search(line):
                    raw_label = m.group('label').strip()
                    session['label'] = raw_label
                    session['start'] = m.group('time')
                    is_orchestrator = True

                    # --- ЧЕСТНАЯ ЛОГИКА ОПРЕДЕЛЕНИЯ ТИПА И МАРКЕРОВ ---
                    m_tag = re.search(r'\[(.*?)\]', raw_label)
                    if m_tag:
                        sc_id = m_tag.group(1)
                        session['scenario_id'] = sc_id
                        
                        # 1. Ищем конфиг: в корне манифеста или в подсекции scenarios
                        sc_config = self.config.get(sc_id) or self.config.get('scenarios', {}).get(sc_id) or {}
                        
                        # 2. Определяем базовый тип (single/series)
                        detected_type = sc_config.get('type')
                        if not detected_type:
                            # Эвристика, если манифест не подгрузился
                            if any(kw in raw_label for kw in ['Stepper', 'Matrix', 'Binary']) or any(kw in sc_id for kw in ['_STEP', '_PD', '_BIN', '_NDR']):
                                detected_type = 'series'
                            else:
                                detected_type = 'single'

                        session['type'] = detected_type.lower()
                        
                        # 3. Извлекаем поведенческий маркер (Behavior) для Фабрики
                        behavior = sc_config.get('behavior')
                        if not behavior:
                            if 'STEP' in sc_id or 'Stepper' in raw_label: 
                                behavior = 'stepper'  # Переменная нагрузка
                            elif 'BIN' in sc_id or 'NDR' in sc_id or 'Binary' in raw_label: 
                                behavior = 'binary'   # Поиск NDR
                            elif 'PD' in sc_id or 'Matrix' in raw_label or 'Degradation' in raw_label: 
                                behavior = 'matrix'   # Постоянная нагрузка, изменение стейта DUT
                            else: 
                                behavior = 'single'
                        
                        session['behavior'] = behavior.lower()

                        Log.info(f"[{self.__class__.__name__}] Parsed Scenario: '{sc_id}' | Type: '{session['type']}' | Behavior: '{session['behavior']}'")
                    continue
                    
                elif m := RE_ITERATION_START.search(line):
                    current_iter = None
                    ensure_iter(m.group('time'), float(m.group('dur')))
                    is_orchestrator = True
                    
                elif not is_orchestrator and (m := RE_MANUAL_TREX.search(line)):
                    t = line[:8] if len(line) >= 8 else "?"
                    ensure_iter(t)['actors'].append({
                        'start': t, 'tool': 'TREX', 'log': f"{m.group('id')}.log", 'profile': m.group('profile'),
                        'load': '?', 'mult': '?', 'artifacts': [{'name': 'TRex Console', 'link': f"{m.group('id')}.log", 'style': 'btn-console'}]
                    })
                    session['label'] = f"TRex: {m.group('profile')}"
                    
                elif not is_orchestrator and (m := RE_MANUAL_JMETER.search(line)):
                    t = line[:8] if len(line) >= 8 else "?"
                    run_id = m.group('id')
                    ensure_iter(t)['actors'].append({
                        'start': t, 'tool': 'JMETER', 'log': f"{run_id}_internal.log", 'profile': m.group('profile'),
                        'load': '?', 'mult': '?', 'artifacts': [
                            {'name': 'JMeter Console', 'link': f"{run_id}_internal.log", 'style': 'btn-console'},
                            {'name': 'HTML Report', 'link': f"{run_id}_report/index.html", 'style': 'btn-primary'}
                        ]
                    })
                    session['label'] = f"JMeter: {m.group('profile')}"
                    
                elif m := RE_ACTOR_SPAWN.search(line):
                    spawn_time = m.group('time') or '00:00:00'
                    it = ensure_iter(spawn_time)
                    tool, log_file = m.group('tool'), m.group('log')
                    if any(a['log'] == log_file for a in it['actors']): continue
                    
                    prof_name = re.sub(r'(_run\d+)?_\d{6}$', '', log_file.replace('.log', ''))
                    if prof_name.lower().startswith(f"{tool.lower()}_"): prof_name = prof_name[len(tool)+1:]
                    
                    artifacts = [{'name': f'{tool} Console', 'link': log_file, 'style': 'btn-console'}]
                    if tool == 'JMETER':
                        base = log_file.replace('.log', '')
                        artifacts.extend([
                            {'name': 'Raw JTL', 'link': f"{base}.jtl", 'style': 'btn'},
                            {'name': 'HTML Report', 'link': f"{base}_report/index.html", 'style': 'btn-primary'}
                        ])
                        
                    it['actors'].append({
                        'start': spawn_time, 'tool': tool, 'log': log_file, 'profile': prof_name,
                        'load': m.group('load') or '?', 'mult': m.group('mult') or '?', 'artifacts': artifacts
                    })
                    
                elif m := RE_ITERATION_END.search(line):
                    if current_iter: current_iter['end'] = m.group('time')
                    current_iter = None

        if session['iterations']: 
            session['end'] = session['iterations'][-1].get('end', '?')
            
        return session

    # ─────────────────────────────────────────────────────────────
    # УТИЛИТЫ (Доступны всем наследникам)
    # ─────────────────────────────────────────────────────────────
    def _read_and_clean_session_log(self, log_path):
        if not os.path.exists(log_path): return "Log file not found."
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
        try:
            with open(log_path, 'r', encoding='utf-8', errors='replace') as f:
                return "".join([ansi_escape.sub('', line) for line in f])
        except Exception as e: return f"Error reading log: {e}"

    def _format_session_label(self, raw_label, session_data):
        m_params = re.search(r'\((Low|Medium|High)\s*\((.*?)\)\)$', raw_label, re.IGNORECASE)
        level, details = (m_params.group(1).strip(), m_params.group(2).strip()) if m_params else ("", "")
        title_part = raw_label[:m_params.start()].strip() if m_params else raw_label

        m_tag = re.search(r'\[(.*?)\]', title_part)
        tag = f"[{m_tag.group(1)}]" if m_tag else ""
        title_part = title_part.replace(m_tag.group(0), '') if m_tag else title_part

        m_desc = re.search(r'\((.*?)\)', title_part)
        desc = f"({m_desc.group(1)})" if m_desc else ""
        title_part = title_part.replace(m_desc.group(0), '') if m_desc else title_part

        clean_title = re.sub(r'^\+\s*|\s*\+$', '', re.sub(r'\s+', ' ', title_part).strip()).strip()
        final_label = f"{tag} {desc}".strip() or clean_title

        if level and details:
            details = re.sub(r'(\d+)m:', r'\1min / ', details)
            details = re.sub(r'(\d+)\s*Mult', lambda m: f"{int(m.group(1))/1000:g} Mpps" if int(m.group(1)) >= 1000 else f"{m.group(1)}k pps", details).replace(',', ' &')
            final_subtitle = f"{clean_title} | {level} Load: {details}" if final_label != clean_title else f"{level} Load: {details}"
        else:
            final_subtitle = "" if final_label == clean_title else clean_title

        return final_label, final_subtitle

    def save_report(self):
        out_path = os.path.join(self.out_dir, f"report_{self.session_id}.html")
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(self.html_output)
        Log.success(f"Report saved: {out_path}")
        self._update_index()

    def _update_index(self):
        import subprocess
        import sys
        index_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'generate_index.py')
        if os.path.exists(index_script):
            subprocess.run([sys.executable, index_script, '--generate'], check=False)

    # ─────────────────────────────────────────────────────────────
    # АБСТРАКТНЫЕ МЕТОДЫ (Должны быть реализованы в наследниках)
    # ─────────────────────────────────────────────────────────────
    @abc.abstractmethod
    def evaluate_metrics(self, data):
        """
        Копирование артефактов (L4/L7 pcap, json, jtl), парсинг статистики 
        инструментов через анализаторы и применение бизнес-логики (SLA, Drops).
        """
        pass

    @abc.abstractmethod
    def render_html(self, evaluated_data):
        """Рендеринг финального HTML (Jinja2 или F-Strings)."""
        pass