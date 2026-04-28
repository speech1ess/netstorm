import importlib
import re
import os
from pydantic import BaseModel
from fastapi import APIRouter, Request

from shared import SharedConfig
from menu_builder import MenuBuilder
import scenario_loader
from tools import profile_explorer
from tools.target_manager import (
    NGINX_ENABLED, CONF_BACKEND, CONF_STATIC, 
    switch_nginx, start_workers, stop_workers
)

router = APIRouter(prefix="/api", tags=["Config & Menu"])

def strip_ansi(text):
    return re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])').sub('', text)

def get_menu_nodes(items):
    """Твой оригинальный парсер меню (с фильтром для веба)"""
    nodes = []
    for item in items:
        if not isinstance(item, dict): continue
        
        # 🟢 ФИЛЬТР ДЛЯ ВЕБ
        if item.get('ui_target') == 'tui_only':
            continue
            
        label = strip_ansi(item.get('label', ''))
        
        if any(word == label.lower().strip() or label.lower().strip().startswith(word + ' ') for word in ['back', 'exit']): 
            continue

        item_type = item.get('type')

        if item_type == 'submenu' and 'items' in item:
            if children := get_menu_nodes(item['items']):
                nodes.append({"type": "folder", "label": label, "children": children})
                
        elif item_type == 'generator':
            func_name = item.get('function')
            if func_name == 'get_program_selector_menu': continue
            try:
                mod = importlib.import_module(item.get('module'))
                if children := get_menu_nodes(getattr(mod, func_name)() ):
                    nodes.append({"type": "folder", "label": label, "children": children})
            except Exception as e:
                print(f"Generator error: {e}")
                
        elif item_type in ['python', 'command', 'exit']:
            # 🟢 ЧИСТАЯ ТАМОЖНЯ: добавили 'is_series' в белый список
            clean_keys = ['type', 'cmd', 'module', 'function', 'args', 'tool', 'confirm_msg', 'is_series', 'custom_schema']
            clean_item = {k: v for k, v in item.items() if k in clean_keys}
            clean_item['label'] = label
            
            if clean_item.get('module') == 'scenario_runner':
                clean_item['is_attack'] = True
                clean_item['scen_id'] = clean_item.get('args', ['Unknown'])[0] if clean_item.get('args') else 'Unknown'
                clean_item['preset'] = clean_item['args'][1] if len(clean_item.get('args', [])) > 1 else 'default'
            else:
                clean_item['is_attack'] = False
                
            nodes.append(clean_item)

    return nodes

@router.get("/configs")
async def get_configs():
    menu_items = scenario_loader.get_program_selector_menu()
    active = scenario_loader._get_active_config()
    available = [item['args'][0] for item in menu_items if item.get('function') == 'set_active_program']
    return {"active_config": active, "available_configs": available}

@router.post("/configs/active")
async def set_active_config(request: Request):
    data = await request.json()
    new_config = data.get('filename')
    if new_config:
        scenario_loader.set_active_program(new_config)
        return {"status": "ok", "active_config": new_config}
    return {"error": "Filename required"}, 400

@router.get("/menu")
async def api_menu():
    fresh_menu_builder = MenuBuilder() 
    return get_menu_nodes(fresh_menu_builder.menu_conf.get('items', []))

@router.get("/profiles")
async def get_profiles_menu():
    """Отдает только дерево профилей для новой вкладки"""
    raw_items = profile_explorer.get_explorer_menu()
    # Пропускаем их через наш парсер, чтобы фронт получил знакомый JSON
    return get_menu_nodes(raw_items)

# ==========================================================
# 🎯 TARGET MANAGER (Переключение режима NGINX)
# ==========================================================
class ModePayload(BaseModel):
    mode: str

@router.get("/target/mode")
async def get_target_mode():
    if os.path.exists(NGINX_ENABLED):
        real_path = os.path.realpath(NGINX_ENABLED)
        if CONF_BACKEND in real_path:
            return {"mode": "backend"}
        if CONF_STATIC in real_path:
            return {"mode": "static"}
    return {"mode": "unknown"}

@router.post("/target/mode")
async def set_target_mode(payload: ModePayload):
    if payload.mode == "backend":
        if switch_nginx(CONF_BACKEND):
            start_workers()
            return {"status": "success", "mode": "backend"}
    elif payload.mode == "static":
        stop_workers()
        if switch_nginx(CONF_STATIC):
            return {"status": "success", "mode": "static"}
    return {"error": "Failed to switch NGINX mode"}, 500

# ==========================================================
# 📄 CONFIG VIEWER (Динамическое чтение файлов)
# ==========================================================
import glob

def _get_allowed_configs():
    """Собирает динамический словарь разрешенных конфигов"""
    allowed_files = {
        "global.yaml": "/opt/pmi/config/global.yaml",
        "trex_cfg.yaml": "/etc/trex_cfg_2.yaml",
        ".env": "/opt/pmi/.env"
    }
    
    # 🟢 Динамически ищем все тестовые программы в папке config
    config_dir = "/opt/pmi/config"
    if os.path.exists(config_dir):
        for file in os.listdir(config_dir):
            if file.endswith(".yaml") and file.startswith("test_program"):
                allowed_files[file] = os.path.join(config_dir, file)
                
    return allowed_files

@router.get("/configs/list")
async def get_config_list():
    """Отдает фронтенду структуру для выпадающего списка"""
    allowed = _get_allowed_configs()
    
    programs = []
    system = []
    
    for filename, path in allowed.items():
        item = {"id": filename, "path": path}
        if filename.startswith("test_program"):
            programs.append(item)
        else:
            system.append(item)
            
    # Сортируем программы по алфавиту
    programs.sort(key=lambda x: x["id"])
    
    return {"programs": programs, "system": system}

@router.get("/configs/content")
async def get_config_content(filename: str):
    """Безопасный роут для чтения только разрешенных конфигов"""
    allowed_files = _get_allowed_configs()
    
    file_path = allowed_files.get(filename)
    if not file_path:
        return {"content": f"# ОШИБКА: Доступ к файлу '{filename}' запрещен или файл не зарегистрирован."}
        
    if not os.path.exists(file_path):
        return {"content": f"# Файл '{file_path}' не найден на диске."}
        
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return {"content": f.read()}
    except Exception as e:
        return {"content": f"# Ошибка чтения файла: {e}"}