# -*- coding: utf-8 -*-
import os
import abc
from shared import SharedConfig
from pmi_logger import Log

class BaseReportStrategy(abc.ABC):
    def __init__(self, session_id, config=None): # <-- Добавили config=None
        self.session_id = session_id
        self.config = config or {}               # <-- Сохранили в класс
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
        
        Log.info("1. Parsing raw logs...")
        self.parsed_data = self.parse_logs()
        
        Log.info("2. Evaluating business logic metrics...")
        self.evaluated_data = self.evaluate_metrics(self.parsed_data)
        
        Log.info("3. Generating HTML...")
        self.html_output = self.render_html(self.evaluated_data)
        
        Log.info("4. Saving artifacts...")
        self.save_report()

    @abc.abstractmethod
    def parse_logs(self):
        """Должен вернуть сырые структурированные данные (RPS, ошибки)"""
        pass

    @abc.abstractmethod
    def evaluate_metrics(self, data):
        """Должен применить бизнес-логику (расставить статусы PASS/FAIL/BLOCKED)"""
        pass

    @abc.abstractmethod
    def render_html(self, evaluated_data):
        """Должен вернуть строку с готовым HTML"""
        pass

    def save_report(self):
        out_path = os.path.join(self.out_dir, f"report_{self.session_id}.html")
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(self.html_output)
        Log.success(f"Report saved: {out_path}")
        
        # Обновляем индекс (можно вынести в утилиты)
        self._update_index()

    def _update_index(self):
        import subprocess
        import sys
        index_script = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'generate_index.py')
        if os.path.exists(index_script):
            subprocess.run([sys.executable, index_script, '--generate'], check=False)