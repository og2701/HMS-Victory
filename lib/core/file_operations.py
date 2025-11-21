import json
import os
from typing import Any

PERSISTENT_VIEWS_FILE = "persistent_views.json"

def load_json_file(filename: str) -> dict:
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json_file(filename: str, data: Any) -> None:
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def load_whitelist() -> list:
    try:
        with open("whitelist.json", "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_whitelist(whitelist: list) -> None:
    with open("whitelist.json", "w") as f:
        json.dump(whitelist, f)

def load_persistent_views() -> dict:
    try:
        with open(PERSISTENT_VIEWS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_persistent_views(data: dict) -> None:
    with open(PERSISTENT_VIEWS_FILE, "w") as f:
        json.dump(data, f)

def set_file_status(file_path: str, active: bool) -> None:
    if active:
        open(file_path, "w").close()
    else:
        if os.path.exists(file_path):
            os.remove(file_path)

def is_file_status_active(file_path: str) -> bool:
    return os.path.exists(file_path)

def read_html_template(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()