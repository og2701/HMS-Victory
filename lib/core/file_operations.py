import json
import os
import tempfile
from typing import Any
from functools import lru_cache
from config import PERSISTENT_VIEWS_FILE, WEBHOOK_DELETIONS_FILE, WHITELIST_FILE

def atomic_write_json(filename: str, data: Any, indent: int = None) -> None:
    """Write JSON durably: serialise to a temp file in the same directory, fsync,
    then os.replace() over the target. A crash mid-write can never leave a
    truncated/corrupt file — readers see either the old or the new content, never
    a half-written one. Used for all persistent JSON state (predictions, persistent
    views, metrics, etc.) that has no other backing store.
    """
    target_dir = os.path.dirname(os.path.abspath(filename))
    os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=target_dir, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, filename)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

def load_json_file(filename: str) -> dict:
    if os.path.exists(filename):
        with open(filename, "r") as f:
            return json.load(f)
    return {}

def save_json_file(filename: str, data: Any) -> None:
    atomic_write_json(filename, data, indent=4)

def load_whitelist() -> list:
    try:
        with open(WHITELIST_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_whitelist(whitelist: list) -> None:
    atomic_write_json(WHITELIST_FILE, whitelist)

def load_persistent_views() -> dict:
    try:
        with open(PERSISTENT_VIEWS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_persistent_views(data: dict) -> None:
    atomic_write_json(PERSISTENT_VIEWS_FILE, data)

def load_webhook_deletions() -> dict:
    try:
        with open(WEBHOOK_DELETIONS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_webhook_deletions(data: dict) -> None:
    atomic_write_json(WEBHOOK_DELETIONS_FILE, data)

def set_file_status(file_path: str, active: bool) -> None:
    if active:
        open(file_path, "w").close()
    else:
        if os.path.exists(file_path):
            os.remove(file_path)

def is_file_status_active(file_path: str) -> bool:
    return os.path.exists(file_path)

@lru_cache(maxsize=32)
def read_html_template(file_path: str) -> str:
    with open(file_path, "r", encoding="utf-8") as file:
        return file.read()