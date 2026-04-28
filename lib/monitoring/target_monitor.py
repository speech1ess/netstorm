#!/usr/bin/env python3
import os
import time
import csv
import json
import threading
import subprocess
from pmi_logger import Log

class TargetMonitor:
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.csv_path = os.path.join(log_dir, "target_metrics.csv")
        self.running = False
        self.thread = None

    def _fetch_metrics(self):
        try:
            # Используем команду проверки из target_manager
            # Таймаут 0.5 сек, чтобы не висеть, если сервер умер
            cmd = ["ip", "netns", "exec", "webserver", "curl", "-s", "-m", "0.5", "http://localhost/"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            
            if res.returncode == 0 and "PMI_JSON_STATS=" in res.stdout:
                # Парсим JSON маркер, игнорируя HTML мусор
                json_str = res.stdout.split("PMI_JSON_STATS=")[1].split('\n')[0].strip()
                return json.loads(json_str)
        except Exception:
            pass
        return None

    def _worker(self):
        start_ts = time.time()
        
        # Инициализируем CSV
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["time_offset", "cpu", "ram"])

        while self.running:
            data = self._fetch_metrics()
            if data:
                offset = round(time.time() - start_ts, 1)
                with open(self.csv_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([offset, data.get('cpu', 0), data.get('ram', 0)])
            
            # 1 RPS - нагрузка минимальная, но разрешение графика отличное
            time.sleep(1)

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        Log.info(f"TargetMonitor: Started (logging to {os.path.basename(self.csv_path)})")

    def stop(self):
        if not self.running: return
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        Log.info("TargetMonitor: Stopped")
#!/usr/bin/env python3
import os
import time
import csv
import json
import threading
import subprocess
from pmi_logger import Log

class TargetMonitor:
    def __init__(self, log_dir):
        self.log_dir = log_dir
        self.csv_path = os.path.join(log_dir, "target_metrics.csv")
        self.running = False
        self.thread = None

    def _fetch_metrics(self):
        try:
            # Используем команду проверки из target_manager
            # Таймаут 0.5 сек, чтобы не висеть, если сервер умер
            cmd = ["ip", "netns", "exec", "webserver", "curl", "-s", "-m", "0.5", "http://localhost/"]
            res = subprocess.run(cmd, capture_output=True, text=True)
            
            if res.returncode == 0 and "PMI_JSON_STATS=" in res.stdout:
                # Парсим JSON маркер, игнорируя HTML мусор
                json_str = res.stdout.split("PMI_JSON_STATS=")[1].split('\n')[0].strip()
                return json.loads(json_str)
        except Exception:
            pass
        return None

    def _worker(self):
        start_ts = time.time()
        
        # Инициализируем CSV
        with open(self.csv_path, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["time_offset", "cpu", "ram"])

        while self.running:
            data = self._fetch_metrics()
            if data:
                offset = round(time.time() - start_ts, 1)
                with open(self.csv_path, 'a', newline='') as f:
                    writer = csv.writer(f)
                    writer.writerow([offset, data.get('cpu', 0), data.get('ram', 0)])
            
            # 1 RPS - нагрузка минимальная, но разрешение графика отличное
            time.sleep(1)

    def start(self):
        if self.running: return
        self.running = True
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()
        Log.info(f"TargetMonitor: Started (logging to {os.path.basename(self.csv_path)})")

    def stop(self):
        if not self.running: return
        self.running = False
        if self.thread:
            self.thread.join(timeout=2)
        Log.info("TargetMonitor: Stopped")