#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import subprocess
import shutil

try:
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log
except ImportError:
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from shared import Colors, SharedConfig, SharedTrap
    from pmi_logger import Log

class JMeterDriver:
    def __init__(self, profile_key, threads, throughput, duration, log_name_base, extra_args):
        self.profile_key = profile_key
        self.threads = threads
        self.throughput = throughput
        self.duration = duration
        self.base_name = log_name_base
        self.extra_args = extra_args
        
        self.session_id = os.environ.get("PMI_RUN_ID")
        self.process = None

    def _resolve_jmx_path(self):
        """Ищет JMX файл по прямому пути или в профилях конфига"""
        if os.path.isfile(self.profile_key) and self.profile_key.endswith('.jmx'):
            return self.profile_key

        test_conf = SharedConfig.load_yaml(os.environ.get("PMI_CURRENT_CONFIG", "test_program.yaml"))
        profile_data = test_conf.get('profiles', {}).get('jmeter', {}).get(self.profile_key)
        
        if not profile_data:
            Log.error(f"JMeter profile '{self.profile_key}' not found in yaml or as direct file.")
            sys.exit(1)

        jmx_file = profile_data.get('jmx')
        base_dir = SharedConfig.get('paths.base', '/opt/pmi')
        profiles_dir = SharedConfig.get('paths.profiles', os.path.join(base_dir, 'profiles'))
        
        jmx_path = os.path.join(profiles_dir, jmx_file)
        if not os.path.exists(jmx_path):
            jmx_path = os.path.join(profiles_dir, 'jmeter', jmx_file)

        if not os.path.exists(jmx_path):
            Log.error(f"JMX file missing: {jmx_path}")
            sys.exit(1)
            
        return jmx_path

    def _prepare_fs(self):
        """Подготавливает директории и очищает старые логи"""
        results_root = SharedConfig.get('paths.results', '/opt/pmi/results')
        out_dir = os.path.join(results_root, self.session_id) if self.session_id else results_root
        os.makedirs(out_dir, exist_ok=True)

        res_file = os.path.join(out_dir, f"{self.base_name}.jtl")
        log_file = os.path.join(out_dir, f"{self.base_name}_internal.log")
        report_dir = os.path.join(out_dir, f"{self.base_name}_report")

        if os.path.exists(report_dir): shutil.rmtree(report_dir)
        if os.path.exists(res_file): os.remove(res_file)
            
        return res_file, log_file, report_dir

    def _build_command(self, jmx_path, res_file, log_file, report_dir):
        """Собирает Bash-команду с учетом инфраструктуры из global.yaml"""
        
        jmeter_bin = SharedConfig.get('nodes.jmeter_node.proc.bin', '/opt/jmeter/bin/jmeter')
        if not jmeter_bin:
            Log.error("JMeter bin file is missing! Check config.")

        # 1. ИЗВЛЕКАЕМ ДАННЫЕ ИНФРАСТРУКТУРЫ
        target_ip = SharedConfig.get('nodes.victim.net.ip', '10.0.40.1')
        
        mon_ip = SharedConfig.get('nodes.monitor.net.ip', '127.0.0.1')
        mon_port = SharedConfig.get('nodes.monitor.services.victoria_api.port', 8428)
        influx_url = f"http://{mon_ip}:{mon_port}/write?db=jmeter"
        
        data_dir = os.path.dirname(os.path.abspath(jmx_path))

        # 🟢 ИЗВЛЕКАЕМ ДИРЕКТОРИЮ И ФАЙЛЫ ДАННЫХ
        datasets = SharedConfig.get('nodes.jmeter_node.datasets', {})
        
        # Умный data_dir: сначала конфиг, потом фоллбэк на папку с профилем
        fallback_dir = os.path.dirname(os.path.abspath(jmx_path))
        data_dir = datasets.get('data_dir', fallback_dir)

        source_ips = datasets.get('source_ips', 'source_ips.csv')
        attack_ips = datasets.get('attack_ips', 'attack_ips.csv')
        uris_file = datasets.get('base_uris', 'uris.csv')

        prof_lower = self.profile_key.lower()
        if any(keyword in prof_lower for keyword in ['base', 'bench']):
            chosen_ips_file = source_ips
            Log.info(f"Mode: BASELINE. Injecting client pool: {os.path.join(data_dir, chosen_ips_file)}")
        else:
            chosen_ips_file = attack_ips
            Log.info(f"Mode: ATTACK. Injecting client pool: {os.path.join(data_dir, chosen_ips_file)}")

        # 3. СОБИРАЕМ КОМАНДУ
        cmd = [
            jmeter_bin,
            "-n", "-t", jmx_path,
            "-l", res_file,
            "-j", log_file,
            f"-JTHREADS={self.threads}",
            f"-JTARGET_RPS={self.throughput}",
            f"-JDURATION_SEC={self.duration}",
            f"-Jrun_id={self.base_name}",
            
            # --- ПРОКИДЫВАЕМ ДИНАМИКУ В JMETER ---
            f"-Jtarget_host={target_ip}",
            f"-JinfluxdbUrl={influx_url}",
            f"-Jdata_dir={data_dir}",
            
            # 🟢 УНИВЕРСАЛЬНАЯ ПЕРЕМЕННАЯ ДЛЯ ПУЛА IP-АДРЕСОВ
            f"-Jips_file={chosen_ips_file}",
            f"-Juris_file={uris_file}",
            # -------------------------------------
            
            "-e", "-o", report_dir
        ]
        
        if self.extra_args: cmd.extend(self.extra_args)

        # 4. Поддержка NetNS
        netns = SharedConfig.get('nodes.jmeter_node.net.netns')
        prefix_msg = ""
        
        if netns:
            if os.path.exists(f"/var/run/netns/{netns}"):
                cmd = ["ip", "netns", "exec", netns] + cmd
                prefix_msg = f" (in netns: {netns})"
            else:
                Log.warning(f"NetNS '{netns}' missing in OS. Running on host.")

        Log.info(f"JMETER DRIVER START: {self.profile_key} (ID: {self.base_name}){prefix_msg}")
        Log.debug(f"CMD: {' '.join(cmd)}")
        return cmd, res_file, log_file

    def _tail_logs(self):
        """Стримит вывод JMeter в консоль Оркестратора"""
        while True:
            line = self.process.stdout.readline()
            if not line and self.process.poll() is not None:
                break
            
            if line:
                line = line.strip()
                if not line: continue
                
                if "summary =" in line or "summary +" in line:
                    Log.info(f"[JMeter] {line}")
                elif "Error" in line or "Exception" in line:
                    Log.error(f"[JMeter] {line}")
                else:
                    Log.debug(f"[JMeter] {line}")

    def cleanup(self):
        if self.process and self.process.poll() is None:
            Log.warning("Terminating JMeter...")
            self.process.terminate()
            try: self.process.wait(timeout=5)
            except: self.process.kill()

    def run(self):
        SharedTrap.register(self.cleanup)
        
        jmx_path = self._resolve_jmx_path()
        res_file, log_file, report_dir = self._prepare_fs()
        cmd, _, _ = self._build_command(jmx_path, res_file, log_file, report_dir)

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, 
                text=True,
                bufsize=1
            )

            self._tail_logs()

            rc = self.process.poll()
            if rc == 0:
                Log.success(f"JMeter finished. JTL: {os.path.basename(res_file)}")
            else:
                Log.error(f"JMeter failed with code {rc}. See {os.path.basename(log_file)}")
                sys.exit(rc)

        except Exception as e:
            Log.error(f"JMeter execution error: {e}")
            self.cleanup()
            sys.exit(1)


# ─────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 6:
        Log.error(f"Usage: {sys.argv[0]} <profile> <threads> <tput> <dur> <LOG_NAME_BASE> [extras...]")
        sys.exit(1)

    driver = JMeterDriver(
        profile_key=sys.argv[1],
        threads=sys.argv[2],
        throughput=sys.argv[3],
        duration=sys.argv[4],
        log_name_base=sys.argv[5],
        extra_args=sys.argv[6:]
    )
    
    driver.run()