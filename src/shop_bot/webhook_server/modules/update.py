import json
import os
import requests
import time

def get_current_version():
    os_json_path = os.path.join(os.path.dirname(__file__), 'os.json')
    with open(os_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['project']['version']

def get_update_url():
    os_json_path = os.path.join(os.path.dirname(__file__), 'os.json')
    with open(os_json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    return data['project']['links']['update']

def parse_version(version_string):
    return tuple(map(int, version_string.split('.')))

def check_for_updates():
    # Автопроверка обновлений ОТКЛЮЧЕНА намеренно.
    # Проект форкнут и сильно кастомизирован (колесо фортуны, правки безопасности и др.),
    # обновление из upstream-шаблона CyberERROR затёрло бы наши изменения. Плюс не нужен
    # исходящий запрос к GitHub из админки. Просто отдаём текущую локальную версию —
    # баннер «Новая версия» и команда-переустановка больше не показываются.
    return {
        'update_available': False,
        'current_version': get_current_version(),
        'latest_version': None,
    }

def register_update_routes(flask_app, login_required):
    @flask_app.route('/update/check', methods=['GET'])
    @login_required
    def check_updates_route():
        result = check_for_updates()
        return result
