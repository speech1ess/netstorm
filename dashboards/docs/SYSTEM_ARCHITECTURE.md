# Архитектура Системы и Структура Файлов PMI Orchestrator v3.0

Этот документ описывает физическую структуру проекта, назначение директорий, а также взаимодействие внутренних модулей Python с внешними бинарными файлами (TRex, JMeter) и подсистемой отчётности.

## 1. Структура Директорий (`/opt/pmi`)

Проект организован в соответствии со стандартом FHS (Filesystem Hierarchy Standard) для автономных приложений.

```text
/opt/pmi/
├── bin/                 # Исполняемые файлы (Entry Points)
│   ├── pmi_start.sh     # Главный скрипт запуска и проверки окружения
│   └── ...              # Вспомогательные скрипты (netns, ip provisioning, sysctl)
│
├── config/              # Конфигурационные файлы (YAML)
│   ├── global.yaml      # Инфраструктура и пути
│   ├── menu_structure.yaml # Базовая структура меню
│   ├── test_program.yaml# Дефолтные сценарии, профили и пресеты
│   └── pmi_*.yaml       # Пользовательские/кастомные программы тестов
│
├── lib/                 # Ядро системы (Python Source Code)
│   ├── menu_builder.py  # Генератор TUI меню (Front-end)
│   ├── scenario_loader.py # [NEW] State-менеджер и динамический парсер конфигов
│   ├── scenario_runner.py # Оркестратор процессов и батч-ранов (Back-end)
│   ├── shared.py        # Общие классы (Config, Log, Colors, Trap)
│   ├── html_templates.py# [NEW] CSS и HTML-шаблоны для отчётов
│   ├── generate_run_summary.py # [NEW] Генератор per-session отчётов и ZIP-архивов
│   ├── generate_index.py# [NEW] Генератор глобального дашборда
│   ├── trex_runner.py   # Wrapper для запуска и мониторинга TRex (L4)
│   └── jmeter_runner.py # Wrapper для запуска и мониторинга JMeter (L7)
│
├── profiles/            # Определения атак (User Data)
│   ├── trex/            # Скрипты TRex Stateless (.py)
│   │   ├── stl_udp_flood.py
│   │   └── stl_syn_flood.py
│   └── jmeter/          # Планы тестирования JMeter (.jmx)
│       ├── 01_l7_get_flood.jmx
│       └── 02_l7_post_upload.jmx
│
├── logs/                # Логи работы (изолированы по сессиям)
│   ├── latest           # Симлинк на последний <SESSION_ID>
│   └── <SESSION_ID>/    # Уникальная папка запуска
│       ├── pmi_session.log         # Главный хронологический лог
│       ├── jmeter_<profile>.log    # Индивидуальные логи акторов
│       └── trex_<profile>.log
│
├── results/             # Артефакты тестов и отчёты
│   ├── index.html       # Глобальный календарный дашборд всех тестов
│   ├── latest           # Симлинк на последний <SESSION_ID>
│   └── <SESSION_ID>/    # Артефакты конкретной сессии
│       ├── report_<SESSION_ID>.html # Локальный сводный HTML-отчёт
│       ├── bundle_<SESSION_ID>.zip  # Архив со всеми логами, JTL и отчётами
│       ├── <actor_id>.jtl           # Сырые метрики JMeter (CSV/XML)
│       └── <actor_id>_report/       # HTML-дашборд сгенерированный самим JMeter
│
└── docs/                # Документация проекта
    ├── CONFIG_ARCHITECTURE.md
    └── SYSTEM_ARCHITECTURE.md
```
## 2. Архитектура Программных Модулей (Python Libs)

Система написана на Python 3 и состоит из слабосвязанных модулей, поддерживающих Data-Driven подход (конфигурация управляет поведением).
### 2.1 Core (Ядро)

    shared.py:

        Роль: Singleton для загрузки конфигурации (SharedConfig), управления цветами консоли (Colors), логирования (Log) и перехвата сигналов Ctrl+C (SharedTrap).

    menu_builder.py:

        Роль: Точка входа. Читает menu_structure.yaml, поддерживает статические пункты, а также умеет вызывать generator функции для построения подменю на лету.

    scenario_loader.py:

        Роль: Менеджер состояния. Сканирует config/, позволяет переключать активную тест-программу (сохраняя выбор в ENV) и динамически генерирует пункты меню со сценариями и их Пресетами (Low, Medium, High).

### 2.2 Orchestration (Оркестрация)

    scenario_runner.py:

        Роль: "Дирижер". Принимает ID сценария и имя пресета. Накладывает overrides пресета на базовый конфиг. Генерирует уникальные ID сессий и акторов. Умеет запускать сценарии как поштучно, так и в режиме Batch Execution (все тесты файла по очереди).

        Принцип: Использует subprocess.Popen для параллельного асинхронного запуска драйверов-обёрток.

### 2.3 Drivers / Wrappers (Драйверы)

Эти скрипты запускаются как отдельные процессы. Они изолируют логику конкретного инструмента от ядра системы.

    trex_runner.py (TRex Driver): Инициализирует API TRex (в т.ч. в L3-режиме с ARP-resolve), применяет multiplier, асинхронно отправляет метрики в VictoriaMetrics и логирует PPS.

    jmeter_runner.py (JMeter Driver): Собирает CLI для Java-процесса. Пробрасывает параметры (threads, throughput, jprops, payload). Форсирует создание JTL и HTML-репортов.

### 2.4 Reporting (Отчётность)

    generate_run_summary.py: Парсит индивидуальные логи акторов (summary =), копирует нужные файлы в results/, собирает единый красивый HTML-отчёт и упаковывает всё в bundle.zip.

    html_templates.py: Хранит HTML/CSS разметку, исключая хардкод в логике генераторов.

## 3. Внешние Зависимости и Бинарники

PMI Orchestrator управляет внешними инструментами, но не включает их в себя. Пути задаются в global.yaml.

    TRex (L4 Traffic Generator): Binary Service (t-rex-64). Взаимодействие через ZMQ порты (Sync/Async) и Python API (trex_stl_lib).

    JMeter (L7 Load Generator): Java Application (ApacheJMeter.jar). Запуск в режиме Non-GUI (-n). Отправляет метрики через BackendListener.

    Monitoring Stack: VictoriaMetrics (принимает Prometheus HTTP POST), Node Exporter, NGINX Exporter. Визуализация в Grafana.

## 4. Поток Данных (Data Flow)

    Config Selection: Пользователь выбирает активный YAML-файл через меню (scenario_loader.py).

    User Input: Выбор сценария и пресета (или запуск Batch Run).

    Session Init: scenario_runner.py создаёт SESSION_ID, готовит папки для логов и применяет параметры нагрузки.

    Process Spawn: Запускаются дочерние драйверы (python trex_runner.py и python jmeter_runner.py).

    Traffic Gen:

        TRex Daemon -> NIC -> Target.

        JMeter Engine -> NIC -> Target.

    Telemetry & Logs: Индивидуальные метрики пишутся в изолированные логи в logs/<SESSION_ID>/. Внешняя телеметрия уходит в VictoriaMetrics.

    Reporting & Archiving: При завершении (или по Ctrl+C) хук вызывает generate_run_summary.py. Скрипт анализирует индивидуальные логи, создаёт HTML-отчёт, копирует файлы и пакует папку в bundle_<SESSION_ID>.zip. Обновляется глобальный дашборд.

## 5. Регламент разработки

При добавлении новых файлов придерживайтесь следующей логики:

    Новые профили атак: Добавляйте скрипты в папку profiles/ и регистрируйте их в секции profiles: вашего файла `test_program_*.yaml` (ПМИ)).

    Новые сценарии: Собирайте их как конструктор в YAML-файле. Код менять не нужно! Используйте presets для управления нагрузкой.

    Новые генераторы трафика: Создайте для инструмента новый Wrapper в lib/ (например, hping_runner.py) и пропишите обработку его параметров в _build_cmd внутри scenario_runner.py.

    Шаблоны отчётов: Любые визуальные правки отчётов делаются строго в lib/html_templates.py. Не забывайте удваивать скобки {{ }} в CSS для совместимости с str.format().
