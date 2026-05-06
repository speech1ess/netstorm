#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import importlib.util

# 1. Жестко прописываем путь к API твоей версии 3.06
TREX_API_PATH = '/opt/trex/v3.06/automation/trex_control_plane/interactive'
if TREX_API_PATH not in sys.path:
    sys.path.insert(0, TREX_API_PATH)

try:
    from trex.astf.api import ASTFClient
except ImportError:
    print(f"❌ ОШИБКА: Не могу найти TRex API по пути {TREX_API_PATH}")
    sys.exit(1)

# Путь к твоему профилю
PROFILE_PATH = "/opt/pmi/profiles/trex/astf_pcap_emixer.py"

def main():
    print("🚀 [RPC Probe] Инициализация ASTF Client...")
    c = ASTFClient(server="127.0.0.1", sync_port=4503, async_port=4502)
    
    try:
        c.connect()
        c.reset()
        c.clear_stats()

        # 2. Динамически грузим твой профиль
        print(f"📦 [RPC Probe] Загрузка профиля {PROFILE_PATH}...")
        spec = importlib.util.spec_from_file_location("astf_mod", PROFILE_PATH)
        astf_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(astf_mod)

        # 3. Эмулируем боевые параметры (Malware ВКЛЮЧЕН)
        tunables = {
            "inject_malware": 1,
            "rampup_sec": 5,
            # Минимальный набор для старта (подставь свои реальные пути, если нужно)
            "payload_dir": "/opt/pmi/payloads/iep" 
        }
        
        profile = astf_mod.register(tunables=tunables)
        c.load_profile(profile)

        # 4. Стартуем трафик на минималках
        print("🔥 [RPC Probe] Старт трафика (mult=1)...")
        c.start(mult=1, duration=10)

        # Ждем, пока ядро создаст потоки и обновит внутреннюю статистику
        print("⏳ [RPC Probe] Ждем 3 секунды для накопления стейта в ядре...")
        time.sleep(3)

        # =========================================================
        # 🟢 МОМЕНТ ИСТИНЫ: ДЕРГАЕМ ЯДРО
        # =========================================================
        print("🔍 [RPC Probe] Запрашиваем get_tg_names() у ядра...")
        
        if hasattr(c, 'get_tg_names'):
            tg_names = c.get_tg_names()
            print(f"📦 [RAW RESPONSE] tg_names = {tg_names}")
            
            if tg_names:
                print("✅ [УСПЕХ] Ядро вернуло список групп! Пытаемся получить статистику...")
                tg_stats = c.get_traffic_tg_stats(tg_names)
                print(json.dumps(tg_stats, indent=2))
            else:
                print("❌ [ПРОБЛЕМА ВЕНДОРА] Ядро вернуло ПУСТОЙ список или None.")
        else:
            print("❌ [УСТАРЕВШЕЕ API] Метод get_tg_names() вообще отсутствует в этой версии библиотеки Python.")
        # =========================================================

    except Exception as e:
        print(f"💀 КРИТИЧЕСКАЯ ОШИБКА: {e}")
    finally:
        if c.is_connected():
            c.stop()
            c.disconnect()
            print("🛑 [RPC Probe] Клиент отключен.")

if __name__ == "__main__":
    main()