# Архитектура Конфигурации PMI Orchestrator v3.0

Этот документ описывает устройство системы конфигурации PMI Orchestrator. Система построена на модульном принципе, что позволяет менять параметры инфраструктуры (IP, порты) отдельно от логики тестирования (сценарии, профили).

## 1. Обзор файловой структуры

Все конфигурационные файлы расположены в директории `/opt/pmi/config/`.

| Файл | Назначение | Частота изменений |
| :--- | :--- | :--- |
| **`global.yaml`** | **Инфраструктура.** "Железо", сеть, пути, IP-адреса, сервисы. | Редко (при настройке среды) |
| **`test_program.yaml`** | **Логика тестов.** Сценарии атак, профили нагрузок, тайминги. | Часто (тюнинг тестов) |
| **`menu_structure.yaml`**| **Интерфейс.** Структура консольного меню и маппинг команд. | При добавлении функций |

---

## 2. Глобальная конфигурация (`global.yaml`)

Этот файл является "Единым Источником Правды" (Single Source of Truth) для всех скриптов. Скрипты **не должны** содержать захардкоженных IP-адресов или путей — они обязаны читать их отсюда.

### Ключевые секции

#### `paths`
Определяет расположение компонентов системы.

```yaml
paths:
  base: "/opt/pmi"
  logs: "/opt/pmi/logs"
  results: "/opt/pmi/results"
  # Путь к Python интерпретатору (важно для запуска подпроцессов)
  python: "/usr/bin/python3" 
  # Путь к библиотеке TRex API
  lib_trex: "/opt/trex/automation/trex_control_plane/interactive"

```

#### `nodes`

Описывает топологию сети (Генераторы, Жертва, Мониторинг).
У каждого узла есть:

* `net`: Сетевые настройки (IP, Interface, Netns, MAC).
* `services`: Порты сервисов (ZMQ, HTTP API, Exporters).

**Пример (TRex Generator):**

```yaml
nodes:
  l4_attack:
    label: "TRex Generator"
    net:
      ip: "127.0.0.1"
      trex_ports: [0, 1]  # ID портов DPDK карты
    services:
      # Важно: Async - это Publisher (вещает), Sync - RPC (принимает команды)
      zmq_async: { port: 4502, protocol: "tcp" } 
      zmq_sync:  { port: 4503, protocol: "tcp" } 

```

#### `debug`

Глобальный флаг отладки.

* `true`: Скрипты выводят в консоль payload метрик и команды запуска subprocess.
* `false`: "Тихий" режим для продакшена.

---

## 3. Программа тестирования (`test_program.yaml`)

Описывает **логику** атак. Файл разделен на две сущности: Профили (файлы) и Сценарии (действия).

### 3.1 Profiles (Профили)

Связывает логическое имя с физическим файлом скрипта.

```yaml
profiles:
  # L4 Атаки (TRex)
  trex:
    udp_flood_64b:
      script: "stl_udp_flood.py"  # Ищется в /opt/pmi/profiles/trex/ или корне profiles/
      
  # L7 Атаки (JMeter)
  jmeter:
    http_get_flood:
      jmx: "01_l7_get_flood.jmx"  # Ищется в /opt/pmi/profiles/jmeter/
      # Дефолтные значения (можно переопределить в сценарии)
      threads: 10
      throughput: 1000

```

### 3.2 Scenarios (Сценарии)

Сценарий — это оркестрация одного или нескольких профилей.

**Параметры:**

* `label`: Человекочитаемое название.
* `duration`: Общая длительность (сек).
* `actors`: Список инструментов ("акторов"), участвующих в тесте.

**Параметры Actor:**

* `tool`: `trex` или `jmeter`.
* `profile`: Ключ из секции `profiles`.
* `delay`: Задержка старта (в секундах).
* `overridemult` (для TRex): Переопределение базового множителя нагрузки (число).
* `threads` / `override_tput` (для JMeter): Переопределение потоков и целевого RPS.
* `jprops` (для JMeter): Словарь для передачи кастомных свойств (`-JKEY=VALUE`) в JMX-сценарий.
* `payload` (для JMeter): Переопределение пути к файлу полезной нагрузки.

**Пример (Сложная атака M3):**

```yaml
scenarios:
  M3:
    label: "Complex (UDP + Legit)"
    duration: 300
    actors:
      # 1. Запускаем фоновый UDP шум (TRex)
      - tool: "trex"
        profile: "udp_flood_64b"
        overridemult: 10
        
      # 2. Через 5 сек подключаем легитимный трафик (JMeter)
      - tool: "jmeter"
        profile: "http_get_flood"
        delay: 5
        threads: 50

```

---

## 4. API для работы с конфигами (Python)

Для использования в коде применяется класс `SharedConfig` из `lib/shared.py`.

### Инициализация и чтение

```python
from shared import SharedConfig

# Глобальный конфиг (global.yaml) загружается автоматически
conf = SharedConfig.global_conf

# Чтение значения с точечной нотацией (Dot Notation)
# Синтаксис: get('section.subsection.key', default_value)
victim_ip = SharedConfig.get('nodes.victim.net.ip', '127.0.0.1')
is_debug = SharedConfig.get('debug', False)

```

### Загрузка Test Program

```python
# Загрузка специфичного конфига (кэшируется)
test_conf = SharedConfig.load_yaml('test_program.yaml')

# Получение данных сценария
scenario = test_conf.get('scenarios', {}).get('U1')

```

---

## 5. Инструкция: Добавление нового теста (v3.0+)

Благодаря динамическому загрузчику `scenario_loader`, добавление нового теста больше не требует правок Python-кода.

1.  **Создайте файл профиля:**
    *   Для TRex: положите `.py` файл в `/opt/pmi/profiles/trex/`.
    *   Для JMeter: положите `.jmx` файл в `/opt/pmi/profiles/jmeter/`.

2.  **Зарегистрируйте Профиль:**
    *   Откройте `test_program.yaml`.
    *   Добавьте запись в секцию `profiles -> trex` или `profiles -> jmeter`.

3.  **Создайте Сценарий:**
    *   В `test_program.yaml` создайте новый блок в `scenarios` (например, `H4`).
    *   Опишите `actors`, ссылаясь на созданный профиль.

4.  **Готово!** При следующем запуске оркестратора (`pmi_start.sh`) система автоматически обнаружит новый сценарий и добавит его в меню `Test & Scenarios Execution -> PMI Scenarios`.

---

## 6. Диагностика проблем

Если конфигурация не читается или тесты не запускаются:

1. **System Diagnostics:** Запустите диагностику из главного меню. Она проверит наличие файлов и валидность путей.
2. **Debug Mode:** Установите `debug: true` в `global.yaml`. Это покажет точные команды запуска процессов.
3. **YAML Lint:** Убедитесь, что отступы в YAML файлах выполнены **пробелами** (обычно 2 пробела), а не табуляцией.

```

```