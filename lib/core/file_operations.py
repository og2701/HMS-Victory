from config import PERSISTENT_VIEWS_FILE, WEBHOOK_DELETIONS_FILE, WHITELIST_FILE

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
        with open(WHITELIST_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_whitelist(whitelist: list) -> None:
    with open(WHITELIST_FILE, "w") as f:
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

def load_webhook_deletions() -> dict:
    try:
        with open(WEBHOOK_DELETIONS_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_webhook_deletions(data: dict) -> None:
    with open(WEBHOOK_DELETIONS_FILE, "w") as f:
        json.dump(data, f)

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