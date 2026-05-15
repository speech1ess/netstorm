import json
import socket
import struct
import logging
from threading import Thread

class ZabbixTrapper:
    """Чистый Python-клиент для Zabbix Trapper (без внешних зависимостей)"""
    
    def __init__(self, zabbix_ip="10.207.87.13", zabbix_port=10051, target_host="trex-1"):
        self.zabbix_ip = zabbix_ip
        self.zabbix_port = zabbix_port
        self.target_host = target_host
        self.logger = logging.getLogger("ZabbixTrapper")

    def _send_sync(self, key: str, value: str):
        """Внутренний метод отправки пакета по протоколу Zabbix Sender"""
        try:
            # Формируем Payload
            payload = {"request": "sender data", "data": [{"host": self.target_host, "key": key, "value": value}]}
            json_data = json.dumps(payload).encode('utf-8')
            
            # Формируем бинарный заголовок протокола Zabbix: ZBXD\x01 + длина данных (8 байт, little-endian)
            header = b'ZBXD\x01' + struct.pack('<Q', len(json_data))
            packet = header + json_data

            # Отправляем в TCP сокет
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0) # Таймаут 3 секунды, чтобы не вешать тесты
                s.connect((self.zabbix_ip, self.zabbix_port))
                s.sendall(packet)
                
            self.logger.info(f"✅ [Zabbix] Отправлен трап: {key} -> {value}")
        except Exception as e:
            self.logger.warning(f"⚠️ [Zabbix] Ошибка отправки трапа ({key}): {e}")

    def notify(self, key: str, value: str):
        """Публичный метод. Запускает отправку в отдельном потоке (Fire & Forget)"""
        # Запускаем в Thread, чтобы ни на миллисекунду не тормозить основной код Раннера
        Thread(target=self._send_sync, args=(key, value), daemon=True).start()